import os
import time
import json
import re
import logging
import io
import uuid
import fitz  # PyMuPDF
from PIL import Image
from google import genai

def sanitize_filename(filename, max_length=100):
    """
    Rende il nome del file a prova di bomba. 
    Fallback garantito se il nome originale è composto solo da caratteri invalidi.
    """
    basename = os.path.basename(filename)
    name, ext = os.path.splitext(basename)
    
    safe_name = re.sub(r'[^a-zA-Z0-9\-_]', '_', name)
    safe_name = re.sub(r'_+', '_', safe_name).strip('_')
    
    if not safe_name:
        safe_name = "documento"
        
    safe_ext = re.sub(r'[^a-zA-Z0-9]', '', ext)[:5]
    safe_ext = f".{safe_ext}" if safe_ext else ""
    
    return f"{safe_name[:max_length]}{safe_ext}"

def sanitize_input(testo):
    if not isinstance(testo, str): return testo
    return testo.strip()

def pulisci_codice_fiscale_piva(valore):
    if not valore or valore == 'ERRORE IA': return ""
    v = str(valore).upper()
    v = re.sub(r'[^A-Z0-9]', '', v)
    if v.startswith('PIVA'): v = v[4:]
    elif v.startswith('IVA'): v = v[3:]
    return v[:50]

def parse_euro(val):
    v = str(val).replace('€', '').strip()
    if ',' in v and '.' in v: v = v.replace('.', '').replace(',', '.')
    elif ',' in v: v = v.replace(',', '.')
    try: return float(v)
    except: return 0.0

def str_to_bool(val):
    if isinstance(val, bool): return val
    return str(val).lower() in ('true', '1', 't', 'y', 'yes')

def valida_e_normalizza_json(parsed_json):
    if isinstance(parsed_json, dict): parsed_json = [parsed_json]
    elif not isinstance(parsed_json, list): raise ValueError("Il payload AI non è una lista.")
        
    json_validato = []
    for item in parsed_json:
        if not isinstance(item, dict): continue
        doc_pulito = {
            "direzione": str(item.get("direzione") or "USCITA").upper(),
            "tipo_documento": str(item.get("tipo_documento") or "FATTURA").upper(),
            "fornitore": str(item.get("fornitore") or "N/D"),
            "numero_fattura": str(item.get("numero_fattura") or ""),
            "piva": str(item.get("piva") or ""),
            "codice_fiscale": str(item.get("codice_fiscale") or ""),
            "data": str(item.get("data") or ""),
            "data_scadenza": str(item.get("data_scadenza") or ""),
            "totale": item.get("totale") if item.get("totale") is not None else 0.0,
            "iva_perc": str(item.get("iva_perc") or ""),
            "iva_euro": item.get("iva_euro") if item.get("iva_euro") is not None else 0.0,
            "ritenuta_acconto": item.get("ritenuta_acconto") if item.get("ritenuta_acconto") is not None else 0.0,
            "flag_estero": str_to_bool(item.get("flag_estero", False)),
            "categoria_contabile": str(item.get("categoria_contabile") or ""),
            "descrizione": str(item.get("descrizione") or ""),
            "richiede_xml": str_to_bool(item.get("richiede_xml", False)),
            "nuovo_nome_file": str(item.get("nuovo_nome_file") or ""),
            "confidence_score": item.get("confidence_score") if item.get("confidence_score") is not None else 0,
            "pagine_sorgente": item.get("pagine_sorgente") or []
        }
        
        if doc_pulito["fornitore"] in ["", "N/D", "ERRORE IA"] or float(str(doc_pulito["totale"]).replace(',','.')) == 0.0:
            try:
                score_attuale = int(float(str(doc_pulito["confidence_score"]).replace('%','')))
                doc_pulito["confidence_score"] = min(score_attuale, 65)
            except: doc_pulito["confidence_score"] = 65
        json_validato.append(doc_pulito)
        
    if not json_validato: raise ValueError("JSON AI vuoto o malformato.")
    return json_validato

def processa_singolo_documento(cursor, file_path, nome_originale, studio_id, utente_id, azienda, api_key, categorie_ia):
    client = genai.Client(api_key=api_key)
    # ====================================================
    # BYPASS SEGRETO: FLUSSO ASINCRONO PER OPERATORE XML
    # ====================================================
    if azienda == "XML_OPERATOR_JOB":
        if not os.path.exists(file_path): 
            raise FileNotFoundError(f"File fisico non trovato: {file_path}")
            
        with open(file_path, "rb") as f: file_bytes = f.read()
        
        to_ai = []
        if file_path.lower().endswith('.pdf'):
            with fitz.open(stream=file_bytes, filetype="pdf") as pdf:
                for page_num in range(len(pdf)):
                    mat = fitz.Matrix(2, 2)
                    pix = pdf[page_num].get_pixmap(matrix=mat)
                    to_ai.append(Image.open(io.BytesIO(pix.tobytes("png"))))
        else:
            to_ai.append(Image.open(io.BytesIO(file_bytes)))
            
        prompt_xml = """
        Sei un estrattore dati per fatturazione elettronica. Leggi il documento allegato ed estrai i dati ESATTAMENTE in questo formato JSON (nessun testo extra, nessuna spiegazione). Se mancano dati, usa stringhe vuote o 0.0.
        {
          "progressivo": "1", "pec_destinatario": "0000000",
          "dati_generali": {"data": "YYYY-MM-DD", "numero": "", "importo_totale": 0.0},
          "linee": [
            {"descrizione": "NOME PRODOTTO", "quantita": 0.0, "um": "Pz", "prezzo_unit": 0.0, "prezzo_tot": 0.0, "iva": 4.0}
          ],
          "riepilogo": {"aliquota_iva": 4.0, "imponibile": 0.0, "imposta": 0.0},
          "pagamento": {"importo": 0.0}
        }
        """
        to_ai.insert(0, prompt_xml)
        
        # Eliminiamo la regex fragile e forziamo nativamente il JSON
        resp = client.models.generate_content(
            model="gemini-3-flash-preview", 
            contents=to_ai,
            config={"response_mime_type": "application/json"}
        )
        
        draft_ia = json.loads(resp.text)
        
        # Salva il file JSON accanto al PDF per farlo leggere ad app.py
        json_path = file_path + ".json"
        with open(json_path, "w") as jf:
            json.dump(draft_ia, jf)
            
        return {"status": "success", "estratti": 1}
    # ====================================================
    # FINE BYPASS XML - INIZIO FLUSSO NORMALE CONTABILITÀ
    # ====================================================

    # Qui continua il tuo codice originale...
    cursor.execute("SELECT * FROM aziende WHERE nome = %s AND studio_id = %s", (azienda, studio_id))
    info_az = cursor.fetchone() or {}
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File fisico non trovato in QUEUE: {file_path}")

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    to_ai = []
    num_pagine_file = 1
    
    if file_path.lower().endswith('.pdf'):
        with fitz.open(stream=file_bytes, filetype="pdf") as pdf:
            num_pagine_file = len(pdf)
            for page_num in range(num_pagine_file):
                mat = fitz.Matrix(2, 2)
                pix = pdf[page_num].get_pixmap(matrix=mat)
                img_pagina = Image.open(io.BytesIO(pix.tobytes("png")))
                to_ai.append(f"--- INIZIO PAGINA {page_num + 1} ---")
                to_ai.append(img_pagina)
    else:
        to_ai.append(Image.open(io.BytesIO(file_bytes)))

    nome_az_contesto = info_az.get('nome', azienda)
    piva_az_contesto = info_az.get('partita_iva', '')
    cat_elenco = ", ".join(categorie_ia)

    # Inserisci il prompt originale qui
    prompt = f"""
                                            Sei un REVISORE CONTABILE SENIOR esperto di fiscalità italiana.

                                            AZIENDA TITOLARE:
                                            Nome: {nome_az_contesto}
                                            P.IVA: {piva_az_contesto}

                                            INPUT:
                                            Ricevi immagini di pagine appartenenti a uno o più documenti fiscali.
                                            Le pagine sono numerate implicitamente in ordine.

                                            ━━━━━━━━━━━━━━━━━━
                                            ISTRUZIONI DI ELABORAZIONE
                                            ━━━━━━━━━━━━━━━━━━
                                            1) Analizza internamente ogni pagina. Non stampare il ragionamento.
                                            2) Identifica documenti fiscali distinti.
                                            3) Unisci pagine nello stesso documento SOLO se:
                                            - Numero documento identico
                                            - P.IVA emittente identica
                                            - Totale coerente
                                            - Nessuna nuova intestazione documento
                                            4) Se lo stesso documento compare due volte (es. scansione doppia): estrailo UNA SOLA VOLTA.
                                            5) Se una pagina non è un documento fiscale (es. estratto conto, preventivo), ignorala.

                                            ━━━━━━━━━━━━━━━━━━
                                            CLASSIFICAZIONE OBBLIGATORIA E REGOLE FISCALI
                                            ━━━━━━━━━━━━━━━━━━
                                            Valori ammessi per "tipo_documento":
                                            FATTURA
                                            FATTURA_ESTERA
                                            NOTA_CREDITO
                                            AUTOFATTURA
                                            PRESTAZIONE_OCCASIONALE
                                            DOCUMENTO_COMMERCIALE
                                            ALTRO

                                            REGOLE FISCALI CRITICHE:
                                            - FATTURA_ESTERA: Usa questa per qualsiasi fattura ricevuta da un fornitore non italiano (es. Google, Amazon, Meta, extra-UE). 
                                              ATTENZIONE: Se in questa fattura estera trovi diciture come "Reverse Charge", "Art. 17" o "Inversione Contabile", DEVI impostare "richiede_xml": true e inserire la scritta "[REVERSE CHARGE RILEVATO] " all'inizio del campo "descrizione".
                                            - AUTOFATTURA: Usa questa SOLO per documenti emessi da {nome_az_contesto} verso se stessa (denunce interne vere e proprie). NON usarla MAI per fatture ricevute da terzi.
                                            - Imposta sempre flag_estero = true se P.IVA non italiana o paese estero.
                                            - Valori ammessi per "categoria_contabile": scegli ESCLUSIVAMENTE tra: {cat_elenco}

                                            ━━━━━━━━━━━━━━━━━━
                                            REGOLE DATI
                                            ━━━━━━━━━━━━━━━━━━
                                            - DIREZIONE:
                                              Emesso DA {nome_az_contesto} → "ENTRATA"
                                              Intestato A {nome_az_contesto} o scontrino → "USCITA"
                                            - FORNITORE:
                                              Inserisci SEMPRE la controparte.
                                              Non inserire mai {nome_az_contesto}.
                                            - DATE:
                                              Formato YYYY-MM-DD.
                                              Se incerta → stringa vuota.
                                            - NUMERI:
                                              Solo numeri.
                                              Decimali con punto.
                                              Nessun simbolo valuta.
                                              Nessuna virgola migliaia (es. 1500.50).
                                            - IVA:
                                              iva_perc solo numero (es. 22, 10, 0)
                                            - BOOLEANI:
                                              true / false (minuscolo)
                                            - DATI INCERTI:
                                              Non inventare. Lascia vuoto o 0.0.
                                            - confidence_score:
                                              Numero da 0 a 100 basato su chiarezza e completezza.

                                            ━━━━━━━━━━━━━━━━━━
                                            OUTPUT
                                            ━━━━━━━━━━━━━━━━━━
                                            Restituisci ESCLUSIVAMENTE un JSON valido.
                                            Nessun testo prima o dopo.
                                            Nessuna spiegazione.
                                            Nessun commento.

                                            [
                                              {{
                                                "direzione": "",
                                                "tipo_documento": "",
                                                "fornitore": "",
                                                "numero_fattura": "",
                                                "piva": "",
                                                "codice_fiscale": "",
                                                "data": "",
                                                "data_scadenza": "",
                                                "totale": 0.0,
                                                "iva_perc": "",
                                                "iva_euro": 0.0,
                                                "ritenuta_acconto": 0.0,
                                                "flag_estero": false,
                                                "categoria_contabile": "",
                                                "descrizione": "",
                                                "richiede_xml": false,
                                                "nuovo_nome_file": "",
                                                "confidence_score": 0,
                                                "pagine_sorgente": []
                                              }}
                                            ]
                                            """
    to_ai.insert(0, prompt)
    
    resp = client.models.generate_content(model="gemini-3-flash-preview", contents=to_ai)
    raw_text = resp.text.strip()
    
    json_match = re.search(r'\[.*\]', raw_text, re.DOTALL)
    if json_match: raw = json_match.group(0)
    else:
        json_match_single = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if json_match_single: raw = "[" + json_match_single.group(0) + "]"
        else:
            raw = raw_text.replace("```json","").replace("```","").strip()
            if not raw.startswith("["): raw = "[" + raw + "]"
    
    raw_js = json.loads(raw)
    lista_js = valida_e_normalizza_json(raw_js)

    # ID Univoco per i file generati al posto di time.time()
    batch_file_uuid = uuid.uuid4().hex
    documenti_validi = 0
    file_generati = []
    
    for idx_js, js in enumerate(lista_js):
        score_raw = js.get('confidence_score', 0)
        try: score = int(float(str(score_raw).replace('%','')))
        except: score = 0
        
        if score < 70 or js.get('fornitore') == 'ERRORE IA': 
            stato_doc = 'errore_ai' if js.get('fornitore') == 'ERRORE IA' else 'inviato_per_validazione'
            richiede_ver = 1
        elif 70 <= score < 90: 
            stato_doc = 'inviato_per_validazione'
            richiede_ver = 1
        else: 
            stato_doc = 'analizzato'
            richiede_ver = 0

        tot_val = abs(parse_euro(js.get('totale', 0)))
        iva_val = abs(parse_euro(js.get('iva_euro', 0)))
        rit_val = abs(parse_euro(js.get('ritenuta_acconto', 0)))
        tipo_doc_val = sanitize_input(js.get('tipo_documento', 'FATTURA'))
        flag_estero_val = str_to_bool(js.get('flag_estero', False))
        dir_db_singolo = js.get('direzione', 'USCITA').upper()
        safe_num_fattura = sanitize_input(js.get('numero_fattura', ''))[:50]
        piva_pulita = pulisci_codice_fiscale_piva(js.get('piva', ''))
        data_doc_estratta = js.get('data', '')
        
        if safe_num_fattura and piva_pulita and data_doc_estratta:
            cursor.execute("""
                SELECT id FROM analisi 
                WHERE studio_id = %s AND azienda = %s AND piva = %s AND numero_fattura = %s AND data_doc = %s LIMIT 1
            """, (studio_id, azienda, piva_pulita, safe_num_fattura, data_doc_estratta))
            if cursor.fetchone():
                continue

        base_app_dir = os.path.dirname(os.path.abspath(__file__))
        save_dir_file = os.path.join(base_app_dir, "uploads", studio_id, azienda.replace(" ", "_"), dir_db_singolo)
        os.makedirs(save_dir_file, exist_ok=True)
        
        nome_suggerito = js.get('nuovo_nome_file', nome_originale)
        nome_pulito = sanitize_filename(nome_suggerito)
            
        safe_name = f"{batch_file_uuid}_{idx_js}_{nome_pulito}"
        final_file_path = os.path.join(save_dir_file, safe_name)
        
        if file_path.lower().endswith('.pdf'):
            pagine_sorgente = js.get('pagine_sorgente', [])
            if not isinstance(pagine_sorgente, list) or len(pagine_sorgente) == 0:
                with open(final_file_path, "wb") as out_f: out_f.write(file_bytes)
            else:
                try:
                    with fitz.open(stream=file_bytes, filetype="pdf") as original_pdf:
                        new_pdf = fitz.open() 
                        for p_num in pagine_sorgente:
                            idx_0_based = int(p_num) - 1
                            if 0 <= idx_0_based < len(original_pdf):
                                new_pdf.insert_pdf(original_pdf, from_page=idx_0_based, to_page=idx_0_based)
                        if len(new_pdf) > 0: new_pdf.save(final_file_path)
                        else:
                            with open(final_file_path, "wb") as out_f: out_f.write(file_bytes)
                        new_pdf.close()
                except Exception as e:
                    logging.error(f"Errore taglio PDF: {e}")
                    with open(final_file_path, "wb") as out_f: out_f.write(file_bytes)
        else:
            with open(final_file_path, "wb") as out_f: out_f.write(file_bytes)
        
        file_generati.append(final_file_path)

        descrizione_doc = js.get('descrizione', '')
        if len(lista_js) > 1: descrizione_doc = f"[Estratto {idx_js+1}/{len(lista_js)}] {descrizione_doc}"

        data_doc_val = js.get('data', '').strip() or None
        data_scadenza_val = js.get('data_scadenza', '').strip() or data_doc_val

        cursor.execute("""
            INSERT INTO analisi (studio_id, azienda, fornitore, numero_fattura, piva, codice_fiscale, data_doc, data_scadenza, totale, iva_perc, iva_euro, categoria_contabile, descrizione, nuovo_nome_file, file_path, direzione, richiede_xml, stato, richiede_verifica, confidence_score, tipo_documento, ritenuta_acconto, flag_estero) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (studio_id, azienda, sanitize_input(js.get('fornitore', 'N/D')), safe_num_fattura, piva_pulita, pulisci_codice_fiscale_piva(js.get('codice_fiscale', '')), data_doc_val, data_scadenza_val, tot_val, js.get('iva_perc', ''), iva_val, js.get('categoria_contabile', 'Altro'), descrizione_doc, safe_name, final_file_path, dir_db_singolo, str_to_bool(js.get('richiede_xml', True)), stato_doc, richiede_ver, score, tipo_doc_val, rit_val, flag_estero_val))
        
        doc_id_creato = cursor.lastrowid
        cursor.execute("INSERT INTO log_attivita (studio_id, utente_id, azione, documento_id) VALUES (%s, %s, %s, %s)", (studio_id, utente_id, f"Upload AI (Estrazione {idx_js+1})", doc_id_creato))
        documenti_validi += 1

    if documenti_validi > 0:
        mese_corrente = time.strftime("%Y-%m")
        cursor.execute("""
            INSERT INTO consumi_ai (studio_id, azienda, mese_anno, documenti_processati, pagine_processate)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
                documenti_processati = documenti_processati + VALUES(documenti_processati),
                pagine_processate = pagine_processate + VALUES(pagine_processate)
        """, (studio_id, azienda, mese_corrente, documenti_validi, num_pagine_file))
        
    return {"status": "success", "estratti": documenti_validi, "file_generati": file_generati}