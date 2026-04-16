import streamlit as st
import extra_streamlit_components as stx
from google import genai
import fitz  # PyMuPDF
from PIL import Image
import time
import io
import os
import datetime
import pandas as pd
import json
import re
import mysql.connector
from mysql.connector import Error
import xml.etree.ElementTree as ET
import base64
import bcrypt
import logging
import uuid # Da mettere in cima al file app.py se non c'è già
from document_processor import sanitize_filename # Importalo

# --- CONFIGURAZIONE LOGGING E PAGINA ---
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

# L'icona base per chi naviga da PC
st.set_page_config(page_title="AI Doc Studio", layout="wide", page_icon="./logo.png")

# --- NASCONDI INTERFACCIA STREAMLIT E FORZA SFONDO SCURO ---
st.markdown("""
    <style>
        /* Nasconde il menu coi 3 puntini e il footer */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        
        /* Nasconde il pulsante "Deploy" di Streamlit */
        .stDeployButton {display:none;}
        
        /* Rende trasparente la barra in alto MA lascia visibile il pulsante ">>" su mobile! */
        header {background-color: transparent !important;}
        
        /* Forza lo sfondo scuro per evitare flash bianchi intermedi */
        .stApp { background-color: #0e1117 !important; }
        
        /* RIMUOVE LO SPAZIO VUOTO IN CIMA ALLA PAGINA */
        .block-container { padding-top: 0rem !important; padding-bottom: 2rem !important; }

        /* TRADUZIONE BOTTONI FOTOCAMERA STREAMLIT */
        button[title="Take photo"] p, button[aria-label="Take photo"] p {
            display: none !important;
        }
        button[title="Take photo"]::after, button[aria-label="Take photo"]::after {
            content: "📸 Scatta Foto";
            font-size: 16px;
            font-weight: 500;
        }

        button[title="Clear photo"] p, button[aria-label="Clear photo"] p {
            display: none !important;
        }
        button[title="Clear photo"]::after, button[aria-label="Clear photo"]::after {
            content: "🗑️ Rimuovi e Riprova";
            font-size: 16px;
            font-weight: 500;
        }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# INIZIALIZZAZIONE COMPONENTI DI BASE
# ==========================================
cookie_manager = stx.CookieManager()

# Assicuriamoci che il componente abbia il tempo di caricarsi
if "cookies_ready" not in st.session_state:
    st.session_state["cookies_ready"] = True
    time.sleep(0.2)
    st.rerun()

# --- PWA E SIMIL-APP MOBILE ---
st.markdown("""
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    """, unsafe_allow_html=True)

BASE_DIR = os.getcwd()
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
if not os.path.exists(UPLOAD_DIR):
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
    except Exception as e:
        logging.error(f"Errore creazione directory uploads: {e}")

CATEGORIE_IA = ["Utenze", "Carburante", "Cancelleria", "Consulenze", "Merce", "Ristorante", "Spese Mediche", "Attrezzature", "Servizi Web", "Altro"]

# --- FUNZIONI DI SICUREZZA ---
def sanitize_input(testo):
    if not isinstance(testo, str): return testo
    testo_pulito = testo.replace("<", "").replace(">", "")
    testo_pulito = testo_pulito.replace(";", ",")
    return testo_pulito.strip()

def pulisci_codice_fiscale_piva(valore):
    if not valore or valore == 'ERRORE IA': return ""
    v = str(valore).upper()
    v = re.sub(r'[^A-Z0-9]', '', v)
    if v.startswith('PIVA'): v = v[4:]
    elif v.startswith('IVA'): v = v[3:]
    return v[:50]

def hash_password(password_chiara):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_chiara.encode('utf-8'), salt).decode('utf-8')

def verify_password(password_chiara, password_db):
    if not password_db: return False
    try:
        if password_db.startswith('$2b$'):
            return bcrypt.checkpw(password_chiara.encode('utf-8'), password_db.encode('utf-8'))
        return password_chiara == password_db
    except Exception as e:
        logging.error(f"Errore verifica password: {e}")
        return False

def migrate_password_to_hash(username, password_chiara):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE utenti SET password = %s WHERE username = %s", (hash_password(password_chiara), username))
        conn.commit()
        conn.close()

def log_action(studio_id, utente_id, azione, documento_id=None):
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO log_attivita (studio_id, utente_id, azione, documento_id) 
                VALUES (%s, %s, %s, %s)
            """, (studio_id, utente_id, azione, documento_id))
            conn.commit()
        except Exception as e:
            logging.error(f"Errore scrittura log: {e}")
        finally:
            conn.close()

# --- GENERATORE XML FATTURAPA (FIX ESTERO E REVERSE CHARGE) ---
def genera_xml_fatturapa(doc_data, info_azienda):
    attributi_root = {"versione": "FPR12", "xmlns:p": "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"}
    fattura = ET.Element("p:FatturaElettronica", attributi_root)
    header = ET.SubElement(fattura, "FatturaElettronicaHeader")
    
    trasmissione = ET.SubElement(header, "DatiTrasmissione")
    id_trasmittente = ET.SubElement(trasmissione, "IdTrasmittente")
    ET.SubElement(id_trasmittente, "IdPaese").text = "IT"
    piva_azienda = str(info_azienda.get('partita_iva', '12345678901')).replace("IT", "")
    ET.SubElement(id_trasmittente, "IdCodice").text = piva_azienda if piva_azienda else "12345678901"
    
    ET.SubElement(trasmissione, "ProgressivoInvio").text = str(doc_data.get('id', '1'))
    ET.SubElement(trasmissione, "FormatoTrasmissione").text = "FPR12"
    ET.SubElement(trasmissione, "CodiceDestinatario").text = "0000000"
    
    cedente = ET.SubElement(header, "CedentePrestatore")
    cessionario = ET.SubElement(header, "CessionarioCommittente")

    # --- 1. PREPARAZIONE DATI AZIENDA (ITALIANA) ---
    piva_az_pulita = piva_azienda if piva_azienda else "00000000000"
    nome_az = info_azienda.get('nome', 'Azienda N/D')
    ind_az = info_azienda.get('indirizzo', 'N/D')
    cap_az = info_azienda.get('cap', '00000')
    citta_az = info_azienda.get('citta', 'N/D')
    prov_az = info_azienda.get('provincia', '')

    # --- 2. PREPARAZIONE DATI CONTROPARTE (ESTERA O ITALIANA) ---
    piva_raw = str(doc_data.get('piva', '00000000000')).strip().upper()
    # Estraiamo dinamicamente il paese dai primi due caratteri alfabetici (es: MT, DE, FR)
    paese_controparte = piva_raw[:2] if len(piva_raw) >= 2 and piva_raw[:2].isalpha() else "IT"
    piva_codice = piva_raw[2:] if paese_controparte != "IT" else piva_raw.replace("IT", "")
    if not piva_codice: piva_codice = "00000000000"
    nome_controparte = doc_data.get('fornitore', 'Sconosciuto')

    # --- 3. SMISTAMENTO RUOLI (Fattura Attiva vs Passiva Estera) ---
    if doc_data.get('direzione') == 'ENTRATA':
        # FATTURA ATTIVA: L'azienda italiana è il Cedente, il cliente (anche estero) è il Cessionario
        dati_ced = ET.SubElement(cedente, "DatiAnagrafici")
        id_ced = ET.SubElement(dati_ced, "IdFiscaleIVA")
        ET.SubElement(id_ced, "IdPaese").text = "IT"
        ET.SubElement(id_ced, "IdCodice").text = piva_az_pulita
        ET.SubElement(ET.SubElement(dati_ced, "Anagrafica"), "Denominazione").text = nome_az
        
        sede_ced = ET.SubElement(cedente, "Sede")
        ET.SubElement(sede_ced, "Indirizzo").text = ind_az
        ET.SubElement(sede_ced, "CAP").text = cap_az
        ET.SubElement(sede_ced, "Comune").text = citta_az
        if prov_az: ET.SubElement(sede_ced, "Provincia").text = prov_az
        ET.SubElement(sede_ced, "Nazione").text = "IT"

        dati_ces = ET.SubElement(cessionario, "DatiAnagrafici")
        id_ces = ET.SubElement(dati_ces, "IdFiscaleIVA")
        ET.SubElement(id_ces, "IdPaese").text = paese_controparte
        ET.SubElement(id_ces, "IdCodice").text = piva_codice
        ET.SubElement(ET.SubElement(dati_ces, "Anagrafica"), "Denominazione").text = nome_controparte
        
        sede_ces = ET.SubElement(cessionario, "Sede")
        ET.SubElement(sede_ces, "Indirizzo").text = doc_data.get('indirizzo', 'N/D')
        ET.SubElement(sede_ces, "CAP").text = doc_data.get('cap', '00000')
        ET.SubElement(sede_ces, "Comune").text = doc_data.get('citta', 'N/D')
        ET.SubElement(sede_ces, "Nazione").text = paese_controparte

    else:
        # FATTURA PASSIVA (Integrazione / Autofattura Reverse Charge):
        # Il Fornitore estero diviene il Cedente, l'Azienda italiana diviene il Cessionario
        dati_ced = ET.SubElement(cedente, "DatiAnagrafici")
        id_ced = ET.SubElement(dati_ced, "IdFiscaleIVA")
        ET.SubElement(id_ced, "IdPaese").text = paese_controparte
        ET.SubElement(id_ced, "IdCodice").text = piva_codice
        ET.SubElement(ET.SubElement(dati_ced, "Anagrafica"), "Denominazione").text = nome_controparte
        
        sede_ced = ET.SubElement(cedente, "Sede")
        ET.SubElement(sede_ced, "Indirizzo").text = doc_data.get('indirizzo', 'N/D')
        ET.SubElement(sede_ced, "CAP").text = doc_data.get('cap', '00000')
        ET.SubElement(sede_ced, "Comune").text = doc_data.get('citta', 'N/D')
        ET.SubElement(sede_ced, "Nazione").text = paese_controparte

        dati_ces = ET.SubElement(cessionario, "DatiAnagrafici")
        id_ces = ET.SubElement(dati_ces, "IdFiscaleIVA")
        ET.SubElement(id_ces, "IdPaese").text = "IT"
        ET.SubElement(id_ces, "IdCodice").text = piva_az_pulita
        ET.SubElement(ET.SubElement(dati_ces, "Anagrafica"), "Denominazione").text = nome_az
        
        sede_ces = ET.SubElement(cessionario, "Sede")
        ET.SubElement(sede_ces, "Indirizzo").text = ind_az
        ET.SubElement(sede_ces, "CAP").text = cap_az
        ET.SubElement(sede_ces, "Comune").text = citta_az
        if prov_az: ET.SubElement(sede_ces, "Provincia").text = prov_az
        ET.SubElement(sede_ces, "Nazione").text = "IT"


    body = ET.SubElement(fattura, "FatturaElettronicaBody")
    dati_gen = ET.SubElement(ET.SubElement(body, "DatiGenerali"), "DatiGeneraliDocumento")
    
    # --- 4. ASSEGNAZIONE INTELLIGENTE "TIPO DOCUMENTO" ---
    tipo_db = doc_data.get('tipo_documento', 'FATTURA')
    if doc_data.get('direzione') == 'USCITA' and str_to_bool(doc_data.get('richiede_xml', False)):
        tipo_xml = "TD17" # Documento di default per l'Integrazione Reverse Charge Servizi Estero
    elif tipo_db == "NOTA_CREDITO":
        tipo_xml = "TD04"
    else:
        tipo_xml = "TD01"
        
    ET.SubElement(dati_gen, "TipoDocumento").text = tipo_xml
    ET.SubElement(dati_gen, "Divisa").text = "EUR"
    ET.SubElement(dati_gen, "Data").text = str(doc_data.get('data_doc', '2026-01-01'))
    ET.SubElement(dati_gen, "Numero").text = str(doc_data.get('numero_fattura', '1'))

    dati_beni = ET.SubElement(body, "DatiBeniServizi")
    linea = ET.SubElement(dati_beni, "DettaglioLinee")
    ET.SubElement(linea, "NumeroLinea").text = "1"
    ET.SubElement(linea, "Descrizione").text = doc_data.get('descrizione', 'Prestazione professionale o merce')
    imponibile = float(doc_data.get('totale', 0)) - float(doc_data.get('iva_euro', 0))
    ET.SubElement(linea, "PrezzoUnitario").text = f"{imponibile:.2f}"
    ET.SubElement(linea, "PrezzoTotale").text = f"{imponibile:.2f}"
    
    iva = str(doc_data.get('iva_perc', '22')).replace('%','').strip()
    try: 
        iva_float = float(iva)
    except: 
        iva_float = 22.0
    ET.SubElement(linea, "AliquotaIVA").text = f"{iva_float:.2f}"

    # --- 5. PROTEZIONE ANTI-SCARTO (INSERIMENTO NATURA) ---
    if iva_float == 0.0:
        # N6.9 per Reverse charge estero, N2.2 per regime forfettario/prestazione occasionale
        natura = "N6.9" if tipo_xml == "TD17" else "N2.2"
        ET.SubElement(linea, "Natura").text = natura

    riepilogo = ET.SubElement(dati_beni, "DatiRiepilogo")
    ET.SubElement(riepilogo, "AliquotaIVA").text = f"{iva_float:.2f}"
    if iva_float == 0.0:
        ET.SubElement(riepilogo, "Natura").text = natura
        
    ET.SubElement(riepilogo, "ImponibileImporto").text = f"{imponibile:.2f}"
    ET.SubElement(riepilogo, "Imposta").text = f"{float(doc_data.get('iva_euro', 0)):.2f}"

    ET.indent(fattura, space="  ", level=0)
    return ET.tostring(fattura, encoding='utf-8', xml_declaration=True)

def genera_xml_fattura_avanzata(dati):
    attributi_root = {
        "versione": "FPR12", 
        "xmlns:ds": "http://www.w3.org/2000/09/xmldsig#", 
        "xmlns:p": "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2", 
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance", 
        "xsi:schemaLocation": "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2 fatturaordinaria_v1.2.xsd"
    }
    fattura = ET.Element("p:FatturaElettronica", attributi_root)
    header = ET.SubElement(fattura, "FatturaElettronicaHeader")

    # --- DATI TRASMISSIONE ---
    trasmissione = ET.SubElement(header, "DatiTrasmissione")
    id_trasm = ET.SubElement(trasmissione, "IdTrasmittente")
    ET.SubElement(id_trasm, "IdPaese").text = "IT"
    ET.SubElement(id_trasm, "IdCodice").text = "06628860964"
    ET.SubElement(trasmissione, "ProgressivoInvio").text = str(dati.get("progressivo", "1"))
    ET.SubElement(trasmissione, "FormatoTrasmissione").text = "FPR12"
    ET.SubElement(trasmissione, "CodiceDestinatario").text = dati.get("codice_destinatario", "0000000")
    
    pec = dati.get("pec_destinatario", "").strip()
    if pec and pec != "0000000": 
        ET.SubElement(trasmissione, "PECDestinatario").text = pec

    # --- CEDENTE PRESTATORE ---
    cedente = ET.SubElement(header, "CedentePrestatore")
    dati_ced = ET.SubElement(cedente, "DatiAnagrafici")
    id_ced = ET.SubElement(dati_ced, "IdFiscaleIVA")
    ET.SubElement(id_ced, "IdPaese").text = "IT"
    ET.SubElement(id_ced, "IdCodice").text = dati["cedente"]["piva"]
    
    cf_ced = dati["cedente"].get("cf", "").strip()
    if cf_ced:
        ET.SubElement(dati_ced, "CodiceFiscale").text = cf_ced
        
    ET.SubElement(ET.SubElement(dati_ced, "Anagrafica"), "Denominazione").text = dati["cedente"]["denominazione"]
    ET.SubElement(dati_ced, "RegimeFiscale").text = dati["cedente"].get("regime", "RF01")

    sede_ced = ET.SubElement(cedente, "Sede")
    ET.SubElement(sede_ced, "Indirizzo").text = dati["cedente"]["indirizzo"]
    civ_ced = dati["cedente"].get("civico", "").strip()
    if civ_ced:
        ET.SubElement(sede_ced, "NumeroCivico").text = civ_ced
    ET.SubElement(sede_ced, "CAP").text = dati["cedente"]["cap"]
    ET.SubElement(sede_ced, "Comune").text = dati["cedente"]["comune"]
    ET.SubElement(sede_ced, "Provincia").text = dati["cedente"]["provincia"]
    ET.SubElement(sede_ced, "Nazione").text = "IT"

    if dati["cedente"].get("rea_numero"):
        isc_rea = ET.SubElement(cedente, "IscrizioneREA")
        
        rea_uff = dati["cedente"].get("rea_ufficio", "").strip()
        if rea_uff:
            ET.SubElement(isc_rea, "Ufficio").text = rea_uff
            
        ET.SubElement(isc_rea, "NumeroREA").text = str(dati["cedente"]["rea_numero"])
        
        rea_liq = dati["cedente"].get("rea_liquidazione", "").strip()
        if rea_liq:
            ET.SubElement(isc_rea, "StatoLiquidazione").text = rea_liq

    email_ced = dati["cedente"].get("email", "").strip()
    if email_ced:
        ET.SubElement(ET.SubElement(cedente, "Contatti"), "Email").text = email_ced

    # --- CESSIONARIO COMMITTENTE ---
    cessionario = ET.SubElement(header, "CessionarioCommittente")
    dati_ces = ET.SubElement(cessionario, "DatiAnagrafici")
    id_ces = ET.SubElement(dati_ces, "IdFiscaleIVA")
    ET.SubElement(id_ces, "IdPaese").text = "IT"
    ET.SubElement(id_ces, "IdCodice").text = dati["cessionario"]["piva"]
    
    cf_ces = dati["cessionario"].get("cf", "").strip()
    if cf_ces and cf_ces != dati["cessionario"]["piva"]:
        ET.SubElement(dati_ces, "CodiceFiscale").text = cf_ces
        
    anag_ces = ET.SubElement(dati_ces, "Anagrafica")
    if dati["cessionario"].get("nome"):
        ET.SubElement(anag_ces, "Nome").text = dati["cessionario"]["nome"]
        ET.SubElement(anag_ces, "Cognome").text = dati["cessionario"]["cognome"]
    else:
        ET.SubElement(anag_ces, "Denominazione").text = dati["cessionario"]["denominazione"]

    sede_ces = ET.SubElement(cessionario, "Sede")
    ET.SubElement(sede_ces, "Indirizzo").text = dati["cessionario"]["indirizzo"]
    civ_ces = dati["cessionario"].get("civico", "").strip()
    if civ_ces:
        ET.SubElement(sede_ces, "NumeroCivico").text = civ_ces
    ET.SubElement(sede_ces, "CAP").text = dati["cessionario"]["cap"]
    ET.SubElement(sede_ces, "Comune").text = dati["cessionario"]["comune"]
    ET.SubElement(sede_ces, "Provincia").text = dati["cessionario"]["provincia"]
    ET.SubElement(sede_ces, "Nazione").text = "IT"

    # --- TERZO INTERMEDIARIO ---
    terzo = ET.SubElement(header, "TerzoIntermediarioOSoggettoEmittente")
    dati_terzo = ET.SubElement(terzo, "DatiAnagrafici")
    id_terzo = ET.SubElement(dati_terzo, "IdFiscaleIVA")
    ET.SubElement(id_terzo, "IdPaese").text = "IT"
    ET.SubElement(id_terzo, "IdCodice").text = "06628860964"
    ET.SubElement(dati_terzo, "CodiceFiscale").text = "06628860964"
    ET.SubElement(ET.SubElement(dati_terzo, "Anagrafica"), "Denominazione").text = "PA DIGITALE S.P.A."
    ET.SubElement(header, "SoggettoEmittente").text = "TZ"

    # --- BODY E DATI GENERALI ---
    body = ET.SubElement(fattura, "FatturaElettronicaBody")
    gen = ET.SubElement(ET.SubElement(body, "DatiGenerali"), "DatiGeneraliDocumento")
    ET.SubElement(gen, "TipoDocumento").text = "TD01"
    ET.SubElement(gen, "Divisa").text = "EUR"
    ET.SubElement(gen, "Data").text = dati["dati_generali"]["data"]
    ET.SubElement(gen, "Numero").text = str(dati["dati_generali"]["numero"])
    
    importo_totale = float(dati['dati_generali']['importo_totale'])
    ET.SubElement(gen, "ImportoTotaleDocumento").text = f"{importo_totale:.2f}"
    ET.SubElement(gen, "Arrotondamento").text = "0.00"

    # --- DETTAGLIO LINEE ---
    beni = ET.SubElement(body, "DatiBeniServizi")
    imponibile_calcolato = 0.0
    
    for i, linea in enumerate(dati["linee"]):
        dett = ET.SubElement(beni, "DettaglioLinee")
        ET.SubElement(dett, "NumeroLinea").text = str(i+1)
        ET.SubElement(dett, "Descrizione").text = str(linea["descrizione"])
        ET.SubElement(dett, "Quantita").text = f"{float(linea['quantita']):.8f}"
        ET.SubElement(dett, "UnitaMisura").text = str(linea.get("um", "Pz"))
        ET.SubElement(dett, "PrezzoUnitario").text = f"{float(linea['prezzo_unit']):.8f}"
        
        prezzo_tot = float(linea['prezzo_tot'])
        imponibile_calcolato += prezzo_tot
        
        ET.SubElement(dett, "PrezzoTotale").text = f"{prezzo_tot:.8f}"
        ET.SubElement(dett, "AliquotaIVA").text = f"{float(linea['iva']):.2f}"

    # --- RIEPILOGO IVA AUTOMATICO ---
    riep = ET.SubElement(beni, "DatiRiepilogo")
    aliquota_iva = float(dati['riepilogo'].get('aliquota_iva', 4.0))
    imposta_calcolata = imponibile_calcolato * (aliquota_iva / 100)
    
    ET.SubElement(riep, "AliquotaIVA").text = f"{aliquota_iva:.2f}"
    ET.SubElement(riep, "SpeseAccessorie").text = "0.00"
    ET.SubElement(riep, "Arrotondamento").text = "0.00000000"
    ET.SubElement(riep, "ImponibileImporto").text = f"{imponibile_calcolato:.2f}"
    ET.SubElement(riep, "Imposta").text = f"{imposta_calcolata:.2f}"
    ET.SubElement(riep, "EsigibilitaIVA").text = "I"

    # --- DATI PAGAMENTO ---
    nodo_pagamento = ET.SubElement(body, "DatiPagamento")
    ET.SubElement(nodo_pagamento, "CondizioniPagamento").text = "TP02"
    pag = ET.SubElement(nodo_pagamento, "DettaglioPagamento")
    ET.SubElement(pag, "ModalitaPagamento").text = "MP01"
    ET.SubElement(pag, "ImportoPagamento").text = f"{importo_totale:.2f}"

    ET.indent(fattura, space="  ", level=0)
    return ET.tostring(fattura, encoding='UTF-8', xml_declaration=True)

# --- FUNZIONI DATABASE ---
def get_db_connection():
    try:
        return mysql.connector.connect(
            host=st.secrets["DB_HOST"],
            user=st.secrets["DB_USER"],
            password=st.secrets["DB_PASSWORD"],
            database=st.secrets["DB_NAME"]
        )
    except Error as e:
        logging.error(f"Errore connessione DB: {e}")
        st.error("Errore temporaneo di sistema. Riprovare più tardi.")
        return None

def get_aziende(studio_id):
    conn = get_db_connection()
    if not conn: return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT nome FROM aziende WHERE studio_id = %s ORDER BY nome ASC", (studio_id,))
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        logging.error(f"Errore get_aziende: {e}")
        return []
    finally:
        conn.close()

def get_info_azienda(nome_azienda, studio_id):
    conn = get_db_connection()
    if not conn: return {}
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM aziende WHERE nome = %s AND studio_id = %s", (nome_azienda, studio_id))
        info = cursor.fetchone()
        return info or {}
    except Exception as e:
        logging.error(f"Errore get_info_azienda: {e}")
        return {}
    finally:
        conn.close()

def add_azienda(nome, piva, cf, ind, cap, citta, prov, studio_id):
    # --- IL MURO DI SICUREZZA ---
    # Sanitizziamo ogni singolo campo variabile prima che tocchi il DB
    nome  = sanitize_input(nome)
    piva  = sanitize_input(piva)
    cf    = sanitize_input(cf)
    ind   = sanitize_input(ind)
    cap   = sanitize_input(cap)
    citta = sanitize_input(citta)
    prov  = sanitize_input(prov)
    
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO aziende (studio_id, nome, partita_iva, codice_fiscale, indirizzo, cap, citta, provincia) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (studio_id, nome, piva, cf, ind, cap, citta, prov))
            conn.commit()
            log_action(studio_id, st.session_state['user'], f"Creata azienda: {nome}")
        except Exception as e:
            logging.error(f"Errore DB salvataggio azienda: {e}")
            st.error("Impossibile creare l'azienda. Verifica i dati e riprova.")
        finally:
            conn.close()

def get_mappature(studio_id):
    conn = get_db_connection()
    if not conn: return {}
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT categoria_ia, codice_conto, codice_iva FROM mappature_conti WHERE studio_id = %s", (studio_id,))
        return {r['categoria_ia']: r for r in cursor.fetchall()}
    except Exception as e:
        logging.error(f"Errore get_mappature: {e}")
        return {}
    finally:
        conn.close()

def get_rubrica_xml(studio_id):
    conn = get_db_connection()
    if not conn: return {}
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM rubrica_xml WHERE studio_id = %s ORDER BY denominazione ASC, cognome ASC", (studio_id,))
        rubrica = {}
        for row in cursor.fetchall():
            chiave = row['denominazione'] if row['denominazione'] else f"{row['nome']} {row['cognome']}".strip()
            if chiave:
                rubrica[chiave] = row
        return rubrica
    except Exception as e:
        logging.error(f"Errore get_rubrica_xml: {e}")
        return {}
    finally:
        conn.close()

def add_rubrica_xml(studio_id, den, nome, cogn, piva, cf, ind, civ, cap, com, prov, pec, email, rea_uff, rea_num, rea_liq):
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO rubrica_xml 
                (studio_id, denominazione, nome, cognome, partita_iva, codice_fiscale, indirizzo, civico, cap, comune, provincia, pec, email, rea_ufficio, rea_numero, rea_liquidazione) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (studio_id, sanitize_input(den), sanitize_input(nome), sanitize_input(cogn), sanitize_input(piva), sanitize_input(cf), sanitize_input(ind), sanitize_input(civ), sanitize_input(cap), sanitize_input(com), sanitize_input(prov), sanitize_input(pec), sanitize_input(email), sanitize_input(rea_uff), sanitize_input(rea_num), sanitize_input(rea_liq)))
            conn.commit()
        except Exception as e:
            logging.error(f"Errore salvataggio rubrica_xml: {e}")
        finally:
            conn.close()

def delete_rubrica_xml(id_contatto, studio_id):
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM rubrica_xml WHERE id = %s AND studio_id = %s", (id_contatto, studio_id))
            conn.commit()
        except Exception as e:
            logging.error(f"Errore eliminazione rubrica_xml: {e}")
        finally:
            conn.close()

def save_mappatura(studio_id, cat, conto, iva):
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM mappature_conti WHERE studio_id=%s AND categoria_ia=%s", (studio_id, cat))
        if cursor.fetchone():
            cursor.execute("UPDATE mappature_conti SET codice_conto=%s, codice_iva=%s WHERE studio_id=%s AND categoria_ia=%s", (conto, iva, studio_id, cat))
        else:
            cursor.execute("INSERT INTO mappature_conti (studio_id, categoria_ia, codice_conto, codice_iva) VALUES (%s, %s, %s, %s)", (studio_id, cat, conto, iva))
        conn.commit()
        conn.close()

def parse_euro(val):
    v = str(val).replace('€', '').strip()
    if ',' in v and '.' in v: v = v.replace('.', '').replace(',', '.')
    elif ',' in v: v = v.replace(',', '.')
    try: 
        return float(v)
    except: 
        return 0.0

def str_to_bool(val):
    if isinstance(val, bool): return val
    return str(val).lower() in ('true', '1', 't', 'y', 'yes')

def valida_e_normalizza_json(parsed_json):
    """
    Validatore di Schema JSON: protegge il backend da allucinazioni dell'AI,
    chiavi mancanti e tipi di dato errati (es. null al posto di stringhe).
    """
    if isinstance(parsed_json, dict):
        parsed_json = [parsed_json]
    elif not isinstance(parsed_json, list):
        raise ValueError("Il payload AI non è una lista valida.")
        
    json_validato = []
    for item in parsed_json:
        if not isinstance(item, dict):
            continue
            
        # Schema garantito: se l'IA omette una chiave o scrive 'null' (None), usiamo un fallback sicuro
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
        
        # --- RECHECK: Fallback Automatico per Affidabilità ---
        # Se mancano Fornitore o Totale, abbassiamo drasticamente il confidence score
        if doc_pulito["fornitore"] in ["", "N/D", "ERRORE IA"] or float(str(doc_pulito["totale"]).replace(',','.')) == 0.0:
            try:
                score_attuale = int(float(str(doc_pulito["confidence_score"]).replace('%','')))
                doc_pulito["confidence_score"] = min(score_attuale, 65) # Forza sotto la soglia di sicurezza (70)
            except:
                doc_pulito["confidence_score"] = 65
                
        json_validato.append(doc_pulito)
        
    if not json_validato:
        raise ValueError("L'IA ha restituito un JSON apparentemente valido ma vuoto o malformato.")
        
    return json_validato

# --- NUOVA LOGICA BILLING PER STUDIO ---
def calcola_fatturazione_studio(totale_documenti, piano="PRO"):
    # Modello B: Piani a soglia aggregata
    if piano == "STARTER":
        base, soglia = 19, 400
    elif piano == "PRO":
        base, soglia = 34, 900
    else:  # BUSINESS
        base, soglia = 59, 2000

    extra_docs = max(0, totale_documenti - soglia)
    costo_extra = extra_docs * 0.05
    return base, soglia, extra_docs, costo_extra, base + costo_extra

# ... (qui ci sono tutte le tue funzioni di supporto: get_db_connection, genera_xml, ecc.)

# --- INIZIALIZZAZIONE SESSIONE ---
if "view" not in st.session_state: st.session_state["view"] = "main"
if "doc_attivo" not in st.session_state: st.session_state["doc_attivo"] = None
if "selected_azienda" not in st.session_state: st.session_state["selected_azienda"] = "--- Scegli Azienda ---"
if "main_tab" not in st.session_state: st.session_state["main_tab"] = "📤 CARICA DOCUMENTI"

try:
    api_key = st.secrets["GEMINI_API_KEY"]
except:
    st.error("Errore Critico: Configurazione di sistema incompleta. Manca la chiave API.")
    st.stop()


# ==========================================
# GESTIONE LOGOUT SICURO (Spostato QUI, dove le funzioni esistono già)
# ==========================================
if st.session_state.get("logout_pending"):
    st.markdown("<br><br><br><h3 style='text-align: center;'>🚪 Disconnessione in corso...</h3>", unsafe_allow_html=True)
    
    if "user" in st.session_state:
        conn = get_db_connection()
        if conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE utenti SET magic_key = NULL WHERE username = %s", (st.session_state["user"],))
            conn.commit()
            conn.close()
    
    st.session_state.clear()
    cookie_manager.delete("ai_doc_studio_key")
    time.sleep(1)
    st.rerun()


# ==========================================
# LA FUNZIONE DI LOGIN DEFINITIVA E BLINDATA
# ==========================================
def check_password():
    # 1. CONTROLLO RAPIDO IN RAM (Il bypass più sicuro)
    if st.session_state.get("authenticated") == True:
        return True

    # 2. LETTURA PASSIVA DEL COOKIE
    magic_key = cookie_manager.get("ai_doc_studio_key")

    # 3. VERIFICA SESSIONE ESISTENTE (Tramite Cookie)
    if magic_key and isinstance(magic_key, str) and len(magic_key) > 10:
        headers = st.context.headers
        current_ua = headers.get("User-Agent", "Unknown-UA")
        raw_ip = headers.get("X-Forwarded-For", headers.get("Remote-Addr", "Unknown-IP"))
        current_ip = raw_ip.split(',')[0].strip()

        conn = get_db_connection()
        if conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("""
                SELECT u.*, s.scadenza_abbonamento 
                FROM utenti u 
                LEFT JOIN studi_commercialisti s ON u.studio_id = s.id_studio 
                WHERE u.magic_key = %s
            """, (magic_key,))
            db_user = cursor.fetchone()
            
            if db_user:
                # Controllo di Sicurezza (Soft Binding)
                db_ip = db_user.get("last_ip", "")
                ip_match = (current_ip == db_ip) or (current_ip in ['127.0.0.1', '::1', 'Unknown-IP'])
                
                db_ua = db_user.get("last_user_agent", "")
                ua_match = (current_ua[:50] == db_ua[:50])
                
                if ip_match and ua_match:
                    if db_user.get("ruolo") != "admin" and db_user.get("scadenza_abbonamento"):
                        if datetime.date.today() > db_user["scadenza_abbonamento"]:
                            st.error("❌ Abbonamento scaduto.")
                            conn.close()
                            return False

                    # Ripristino RAM autorizzato
                    st.session_state.update({
                        "authenticated": True, 
                        "user": db_user["username"], 
                        "studio_id": db_user["studio_id"],
                        "ruolo": db_user.get("ruolo", "cliente"),
                        "nome_azienda": db_user.get("nome_azienda"),
                        "scadenza_abbonamento": db_user.get("scadenza_abbonamento")
                    })
                    conn.close()
                    st.rerun() # Entrata rapida nella dashboard
                else:
                    # Furto sventato
                    st.error("🚨 Violazione di sicurezza rilevata. IP o Browser modificati. Sessione invalidata.")
                    cursor.execute("UPDATE utenti SET magic_key = NULL WHERE magic_key = %s", (magic_key,))
                    conn.commit()
                    conn.close()
                    try: cookie_manager.delete("ai_doc_studio_key")
                    except: pass
                    st.stop()
            else:
                # Cookie vecchio o orfano
                conn.close()

# 4. FORM DI LOGIN COMPATTO E CENTRATO (Se arrivi qui, non sei loggato)
    col_vuota_sinistra, col_centrale, col_vuota_destra = st.columns([1.5, 1, 1.5])
    
    with col_centrale:
        with st.container(border=True): 
            st.title("🔐 Login")
            user = st.text_input("Username").lower()
            password = st.text_input("Password", type="password")
            
            if st.button("Accedi", type="primary", use_container_width=True):
                conn = get_db_connection()
                if conn:
                    try:
                        cursor = conn.cursor(dictionary=True)
                        cursor.execute("SELECT u.*, s.scadenza_abbonamento FROM utenti u LEFT JOIN studi_commercialisti s ON u.studio_id = s.id_studio WHERE u.username = %s", (user,))
                        db_user = cursor.fetchone()
                        
                        if db_user and verify_password(password, db_user["password"]):
                            
                            # Generazione nuova chiave e cattura impronta
                            m_key = base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip('=')
                            headers = st.context.headers
                            current_ua = headers.get("User-Agent", "Unknown-UA")
                            raw_ip = headers.get("X-Forwarded-For", headers.get("Remote-Addr", "Unknown-IP"))
                            current_ip = raw_ip.split(',')[0].strip()

                            cursor.execute("""
                                UPDATE utenti 
                                SET magic_key = %s, last_ip = %s, last_user_agent = %s 
                                WHERE username = %s
                            """, (m_key, current_ip, current_ua, user))
                            conn.commit()

                            # Iniezione in RAM per non perdere i dati al ricaricamento
                            st.session_state.update({
                                "authenticated": True, 
                                "user": db_user["username"], 
                                "studio_id": db_user["studio_id"],
                                "ruolo": db_user.get("ruolo", "cliente"),
                                "nome_azienda": db_user.get("nome_azienda"),
                                "scadenza_abbonamento": db_user.get("scadenza_abbonamento")
                            })
                            
                            # Ordine di salvataggio cookie
                            cookie_manager.set("ai_doc_studio_key", m_key, expires_at=datetime.datetime.now() + datetime.timedelta(days=30))
                            
                            log_action(db_user["studio_id"], db_user["username"], "Login effettuato")
                            conn.close()
                            
                            # Attesa millimetrica per il cookie
                            time.sleep(0.3)
                            st.rerun() 
                        else: 
                            st.error("Credenziali errate")
                    except Exception as db_err:
                        st.error(f"Errore di sistema: {db_err}")
                    finally:
                        if conn and conn.is_connected(): conn.close()
    return False
    
# ==========================================
# ESECUZIONE APP PRINCIPALE CON GLOBAL ERROR HANDLER
# ==========================================
if check_password():
        
    try:
        studio_corrente = st.session_state["studio_id"]
        ruolo_utente = st.session_state["ruolo"]
        
        is_admin = (ruolo_utente == "admin")
        is_commercialista = (ruolo_utente == "commercialista")
        is_cliente = (ruolo_utente == "cliente")
        is_operatore = (ruolo_utente == "operatore_xml")

        if is_commercialista:
            st.markdown(f"<div style='background-color:#0e1117; padding:10px; border-radius:5px; margin-bottom:15px; border-left: 5px solid #28a745;'><small>🔒 Ambiente dedicato con isolamento e protezione avanzata dei dati.</small></div>", unsafe_allow_html=True)
        elif is_cliente:
            st.markdown(f"<div style='background-color:#0e1117; padding:10px; border-radius:5px; margin-bottom:15px; border-left: 5px solid #28a745;'><small>🔒 Ambiente dedicato con isolamento e protezione avanzata dei dati.</small></div>", unsafe_allow_html=True)
        
        st.sidebar.title(f"👤 {st.session_state['user'].capitalize()}")
        if is_cliente: 
            st.sidebar.caption(f"Azienda: {st.session_state['nome_azienda']}")
        else: 
            st.sidebar.caption(f"🏢 Studio: {studio_corrente.upper()}")
        
        st.sidebar.divider()
        if st.sidebar.button("🏠 Area di Lavoro", width="stretch"):
            st.session_state["view"] = "main"
            st.session_state["doc_attivo"] = None
            st.rerun()

        if is_admin:
            st.sidebar.divider()
            if st.sidebar.button("⚙️ Pannello Admin", width="stretch"): 
                st.session_state["view"] = "admin_panel"
                st.rerun()

        if is_commercialista:
            st.sidebar.divider()
            if st.sidebar.button("👥 Gestione Clienti", width="stretch"): 
                st.session_state["view"] = "gestione_studio"
                st.rerun()
            if st.sidebar.button("⚙️ Mappatura Conti", width="stretch"): 
                st.session_state["view"] = "impostazioni"
                st.rerun()
        
        if is_operatore:
            st.sidebar.divider()
            if st.sidebar.button("📝 Generazione XML", width="stretch"):
                st.session_state["view"] = "generatore_xml"
                st.rerun()
        
        st.sidebar.divider()
        
        if st.sidebar.button("👤 Il mio Profilo", width="stretch"):
            st.session_state["view"] = "profilo"
            st.rerun()
            
        # --- PULSANTE DI LOGOUT CORRETTO ---
        if st.sidebar.button("🚪 Logout", width="stretch"):
            log_action(studio_corrente, st.session_state['user'], "Logout")
            
            # NON mettiamo il delete() qui!
            # Impostiamo solo la variabile e forziamo il riavvio,
            # ci penserà il blocco a inizio pagina a eseguire il logout perfetto.
            st.session_state["logout_pending"] = True
            st.rerun()

        # ==========================================
        # VISTA: PROFILO UTENTE
        # ==========================================
        if st.session_state["view"] == "profilo":
            st.title("👤 Gestione Profilo")
            st.subheader(f"Utente connesso: {st.session_state['user']}")
            
            # Setup dei tab dinamici in base al ruolo (Rimosso tab Sicurezza)
            if is_commercialista:
                tab_dati, tab_consumi = st.tabs(["👤 I miei dati", "📈 Consumi"])
            else:
                tab_dati, = st.tabs(["👤 I miei dati"])
                
            with tab_dati:
                # --- 1. SEZIONE DATI ACCOUNT ---
                with st.container(border=True):
                    st.markdown("### Riepilogo Dati Account")
                    conn = get_db_connection()
                    if conn:
                        cursor = conn.cursor(dictionary=True)
                        
                        # Recuperiamo i dati base dell'utente
                        cursor.execute("SELECT * FROM utenti WHERE username = %s", (st.session_state['user'],))
                        dati_utente = cursor.fetchone()
                        
                        c1, c2 = st.columns(2)
                        with c1:
                            st.markdown("##### 👤 Dati Accesso")
                            st.text_input("Username", value=dati_utente['username'], disabled=True)
                            st.text_input("Ruolo di Sistema", value=str(dati_utente['ruolo']).capitalize(), disabled=True)
                            if not is_commercialista and dati_utente.get('nome_azienda'):
                                st.text_input("Azienda Associata", value=dati_utente['nome_azienda'], disabled=True)
                                
                        with c2:
                            st.markdown("##### 🏢 Dati Studio")
                            st.text_input("Codice Studio (ID)", value=dati_utente['studio_id'], disabled=True)
                            
                            if is_commercialista:
                                cursor.execute("SELECT * FROM studi_commercialisti WHERE id_studio = %s", (st.session_state['studio_id'],))
                                dati_studio = cursor.fetchone()
                                if dati_studio:
                                    st.text_input("Ragione Sociale", value=dati_studio.get('ragione_sociale', 'N/D'), disabled=True)
                                    st.text_input("Partita IVA", value=dati_studio.get('partita_iva', 'N/D') or 'N/D', disabled=True)
                                    st.text_input("Piano SaaS Attivo", value=dati_studio.get('piano_tariffario', 'PRO'), disabled=True)
                        
                        conn.close()

                # --- 2. SEZIONE CAMBIO PASSWORD SPOSTATA QUI SOTTO ---
                st.markdown("<br>", unsafe_allow_html=True)
                col1, col2 = st.columns([1, 1.5])
                
                with col1:
                    with st.container(border=True):
                        with st.form("form_cambio_password"):
                            st.markdown("### 🔐 Cambia Password")
                            vecchia_pass = st.text_input("Password Attuale", type="password")
                            nuova_pass = st.text_input("Nuova Password", type="password")
                            conferma_pass = st.text_input("Conferma Nuova Password", type="password")
                            
                            if st.form_submit_button("Aggiorna Password", type="primary", width="stretch"):
                                if not vecchia_pass or not nuova_pass:
                                    st.error("Compila tutti i campi.")
                                elif nuova_pass != conferma_pass:
                                    st.error("Le nuove password non coincidono.")
                                elif len(nuova_pass) < 6:
                                    st.error("La password deve essere lunga almeno 6 caratteri.")
                                else:
                                    conn = get_db_connection()
                                    if conn:
                                        cursor = conn.cursor(dictionary=True)
                                        cursor.execute("SELECT password FROM utenti WHERE username = %s", (st.session_state['user'],))
                                        db_user = cursor.fetchone()
                                        
                                        if db_user and verify_password(vecchia_pass, db_user['password']):
                                            hashed_pw = hash_password(nuova_pass)
                                            cursor.execute("UPDATE utenti SET password = %s WHERE username = %s", (hashed_pw, st.session_state['user']))
                                            conn.commit()
                                            log_action(studio_corrente, st.session_state['user'], "Cambio password autonomo effettuato")
                                            st.success("✅ Password aggiornata con successo! La nuova password è già attiva.")
                                        else:
                                            st.error("❌ La password attuale non è corretta.")
                                        conn.close()
                
                # --- BOTTONE INDIETRO ---
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("⬅️ Torna all'Area di Lavoro", key="btn_back_profilo"):
                    st.session_state["view"] = "main"
                    st.rerun()

            # --- IL BLOCCO DEI CONSUMI RIMANE INTATTO QUI SOTTO ---
            if is_commercialista:
                with tab_consumi:
                    mese_corrente = datetime.date.today().strftime("%Y-%m")
                    
                    conn = get_db_connection()
                    if conn:
                        cursor = conn.cursor(dictionary=True)
                        
                        # Recuperiamo il piano dello studio in modo sicuro (fallback a PRO se la colonna manca)
                        cursor.execute("SELECT * FROM studi_commercialisti WHERE id_studio = %s", (studio_corrente,))
                        res_studio = cursor.fetchone()
                        piano_scelto = res_studio.get('piano_tariffario', 'PRO') if res_studio else 'PRO'
                        
                        # Sommiamo tutti i documenti processati da TUTTE le aziende di questo studio nel mese
                        cursor.execute("""
                            SELECT SUM(documenti_processati) as tot_doc, SUM(pagine_processate) as tot_pag 
                            FROM consumi_ai 
                            WHERE studio_id = %s AND mese_anno = %s
                        """, (studio_corrente, mese_corrente))
                        totali = cursor.fetchone()
                        
                        tot_doc = int(totali['tot_doc']) if totali and totali['tot_doc'] else 0
                        
                        base, soglia, extra_docs, costo_extra, totale_mese = calcola_fatturazione_studio(tot_doc, piano_scelto)
                        
                        # TRUCCO LAYOUT UNIFICATO: Creiamo la colonna al 60% PRIMA di inserire i dati,
                        # così includerà sia le metriche in alto, sia la barra, sia la tabella in basso!
                        col_contenuti, col_vuota = st.columns([1.5, 1])
                        
                        with col_contenuti:
                            # --- AVVISO DI SCADENZA SPOSTATO QUI ---
                            if st.session_state.get("scadenza_abbonamento"):
                                scadenza = st.session_state["scadenza_abbonamento"]
                                giorni_rimanenti = (scadenza - datetime.date.today()).days
                                
                                if giorni_rimanenti < 0:
                                    st.error(f"⚠️ Il tuo abbonamento è scaduto il {scadenza.strftime('%d/%m/%Y')}.")
                                elif giorni_rimanenti == 0:
                                    st.warning(f"⚠️ Attenzione! Il tuo abbonamento scade OGGI ({scadenza.strftime('%d/%m/%Y')}).")
                                elif giorni_rimanenti <= 3:
                                    st.warning(f"⚠️ Attenzione! L'abbonamento scadrà tra {giorni_rimanenti} giorni (il {scadenza.strftime('%d/%m/%Y')}).")
                                else:
                                    st.info(f"📅 Il tuo abbonamento è attivo e scadrà il {scadenza.strftime('%d/%m/%Y')}.")
                            
                            st.markdown("### 📊 Consumi Mensili Aggregati (Tutte le Aziende)")        
                            # Metriche dei consumi
                            c1, c2, c3 = st.columns(3)
                            c1.metric(f"Piano: {piano_scelto}", f"{soglia} doc", "Quota fissa", delta_color="off")
                            c2.metric("Documenti Processati", tot_doc, f"{extra_docs} extra" if extra_docs > 0 else f"{soglia - tot_doc} rimasti", delta_color="inverse" if extra_docs > 0 else "normal")
                            c3.metric("Spesa Extra Mese", f"{costo_extra:.2f} €", f"a 0.05€/doc" if extra_docs > 0 else "Nessun extra", delta_color="inverse" if extra_docs > 0 else "off")
                            
                            st.progress(min(tot_doc / soglia if soglia > 0 else 1.0, 1.0))
                            
                            st.markdown("<br>🏢 Dettaglio Consumi per Azienda", unsafe_allow_html=True)
                            cursor.execute("""
                                SELECT azienda, documenti_processati, last_update 
                                FROM consumi_ai 
                                WHERE studio_id = %s AND mese_anno = %s
                                ORDER BY documenti_processati DESC
                            """, (studio_corrente, mese_corrente))
                            dettaglio = cursor.fetchall()
                            
                            if dettaglio:
                                df_dettaglio = pd.DataFrame(dettaglio)
                                df_dettaglio.columns = ["Nome Azienda", "Documenti Estratti", "Ultimo Upload"]
                                
                                st.dataframe(
                                    df_dettaglio, 
                                    hide_index=True,
                                    column_config={
                                        "Nome Azienda": st.column_config.TextColumn("Nome Azienda", width="medium"),
                                        "Documenti Estratti": st.column_config.NumberColumn("Documenti Estratti", width="small"),
                                        "Ultimo Upload": st.column_config.DatetimeColumn("Ultimo Upload", format="DD/MM/YYYY HH:mm", width="small")
                                    }
                                )
                            else:
                                st.info("Nessun documento processato questo mese.")
                            
                        conn.close()
                
                    st.markdown("<br>", unsafe_allow_html=True)
                    if st.button("⬅️ Torna all'Area di Lavoro", key="btn_back_profilo_consumi"):
                        st.session_state["view"] = "main"
                        st.rerun()
        # ==========================================
        # VISTA: IMPOSTAZIONI TRACCIATI ZUCCHETTI
        # ==========================================
        elif st.session_state["view"] == "impostazioni" and is_commercialista:
            st.title("⚙️ Mappatura Piano dei Conti")
            st.markdown("Abbina le categorie riconosciute dall'Intelligenza Artificiale ai codici esatti del tuo gestionale Zucchetti/TeamSystem.")
            
            with st.container(border=True):
                mappe_attuali = get_mappature(studio_corrente)
                
                dati_tabella = []
                for cat in CATEGORIE_IA:
                    dati_tabella.append({
                        "Categoria IA": cat,
                        "Codice Conto Gestionale": mappe_attuali.get(cat, {}).get('codice_conto', ''),
                        "Codice IVA Default": mappe_attuali.get(cat, {}).get('codice_iva', '')
                    })
                    
                df_mappe = pd.DataFrame(dati_tabella)
                edited_mappe = st.data_editor(df_mappe, hide_index=True, disabled=["Categoria IA"], width="stretch", num_rows="fixed")
                
                st.markdown("<br>", unsafe_allow_html=True)
                col_btn1, col_btn2 = st.columns([1, 3])
                with col_btn1:
                    if st.button("💾 Salva Mappatura", type="primary", width="stretch"):
                        for i, row in edited_mappe.iterrows():
                            save_mappatura(studio_corrente, row["Categoria IA"], sanitize_input(str(row["Codice Conto Gestionale"])), sanitize_input(str(row["Codice IVA Default"])))
                        log_action(studio_corrente, st.session_state['user'], "Modificata mappatura conti")
                        st.success("Mappatura salvata!")

        # ==========================================
        # VISTA: GESTIONE STUDIO E CLIENTI
        # ==========================================
        elif st.session_state["view"] == "gestione_studio" and is_commercialista:
            st.title("👥 Gestione Studio e Clienti")
            with st.container(border=True):
                tab_az, tab_ut, tab_monitoraggio, tab_audit = st.tabs(["🏭 Le mie Aziende", "🔑 Accessi Web Clienti", "🚦 Monitoraggio Consegne", "📜 Registro Audit"])
                
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor(dictionary=True)
                    with tab_az:
                        cursor.execute("SELECT * FROM aziende WHERE studio_id = %s ORDER BY nome ASC", (studio_corrente,))
                        aziende_data = cursor.fetchall()
                        if aziende_data:
                            ca, cp, cc, cact1, cact2 = st.columns([3, 2, 2, 0.5, 0.5])
                            ca.markdown("**Ragione Sociale**")
                            cp.markdown("**Partita IVA**")
                            cc.markdown("**Codice Fiscale**")
                            st.divider()
                            
                            for az in aziende_data:
                                ca, cp, cc, cact1, cact2 = st.columns([3, 2, 2, 0.5, 0.5])
                                ca.write(az['nome'])
                                cp.write(az['partita_iva'] or "-")
                                cc.write(az['codice_fiscale'] or "-")
                                
                                with cact1:
                                    with st.popover("✏️", help="Modifica anagrafica"):
                                        with st.form(key=f"edit_az_comm_{az['nome']}"):
                                            st.markdown(f"**Modifica {az['nome']}**")
                                            e_nome = st.text_input("Ragione Sociale *", value=az['nome'])
                                            e_piva = st.text_input("Partita IVA *", value=az['partita_iva'] or "")
                                            e_cf = st.text_input("Codice Fiscale", value=az['codice_fiscale'] or "")
                                            e_ind = st.text_input("Indirizzo", value=az.get('indirizzo', '') or "")
                                            e_cap = st.text_input("CAP", value=az.get('cap', '') or "")
                                            e_citta = st.text_input("Città", value=az.get('citta', '') or "")
                                            e_prov = st.text_input("Provincia", value=az.get('provincia', '') or "")
                                            
                                            if st.form_submit_button("Salva Modifiche", type="primary", width="stretch"):
                                                if e_nome and e_piva:
                                                    cursor.execute("""
                                                        UPDATE aziende 
                                                        SET nome=%s, partita_iva=%s, codice_fiscale=%s, indirizzo=%s, cap=%s, citta=%s, provincia=%s 
                                                        WHERE nome=%s AND studio_id=%s
                                                    """, (sanitize_input(e_nome), sanitize_input(e_piva), sanitize_input(e_cf), sanitize_input(e_ind), sanitize_input(e_cap), sanitize_input(e_citta), sanitize_input(e_prov), az['nome'], studio_corrente))
                                                    
                                                    if sanitize_input(e_nome) != az['nome']:
                                                        cursor.execute("UPDATE utenti SET nome_azienda=%s WHERE nome_azienda=%s AND studio_id=%s", (sanitize_input(e_nome), az['nome'], studio_corrente))
                                                        cursor.execute("UPDATE analisi SET azienda=%s WHERE azienda=%s AND studio_id=%s", (sanitize_input(e_nome), az['nome'], studio_corrente))
                                                        if st.session_state.get("selected_azienda") == az['nome']: 
                                                            st.session_state["selected_azienda"] = sanitize_input(e_nome)
                                                    
                                                    conn.commit()
                                                    log_action(studio_corrente, st.session_state['user'], f"Modificata azienda: {sanitize_input(e_nome)}")
                                                    st.rerun()
                                                else:
                                                    st.error("Ragione Sociale e P.IVA sono obbligatori!")

                                with cact2:
                                    if st.button("❌", key=f"del_az_comm_{az['nome']}", help="Elimina azienda e dati"):
                                        cursor.execute("DELETE FROM aziende WHERE nome = %s AND studio_id = %s", (az['nome'], studio_corrente))
                                        cursor.execute("DELETE FROM utenti WHERE nome_azienda = %s AND studio_id = %s", (az['nome'], studio_corrente))
                                        cursor.execute("DELETE FROM analisi WHERE azienda = %s AND studio_id = %s", (az['nome'], studio_corrente))
                                        conn.commit()
                                        log_action(studio_corrente, st.session_state['user'], f"Eliminata azienda: {az['nome']}")
                                        if st.session_state.get("selected_azienda") == az['nome']: 
                                            st.session_state["selected_azienda"] = "--- Scegli Azienda ---"
                                        st.rerun()
                        else: 
                            st.info("Non hai ancora registrato nessuna Azienda.")
                    
                    with tab_ut:
                        cursor.execute("SELECT username, nome_azienda FROM utenti WHERE studio_id = %s AND ruolo = 'cliente' ORDER BY nome_azienda ASC", (studio_corrente,))
                        utenti_data = cursor.fetchall()
                        if utenti_data:
                            cu, ca, cact = st.columns([2, 3, 1])
                            cu.markdown("**Username Cliente**")
                            ca.markdown("**Azienda**")
                            cact.markdown("**Azioni**")
                            st.divider()
                            for u in utenti_data:
                                cu, ca, cact = st.columns([2, 3, 1])
                                cu.write(u['username'])
                                ca.write(u['nome_azienda'])
                                if cact.button("❌", key=f"del_u_comm_{u['username']}", help="Revoca l'accesso"):
                                    cursor.execute("DELETE FROM utenti WHERE username = %s AND studio_id = %s", (u['username'], studio_corrente))
                                    conn.commit()
                                    log_action(studio_corrente, st.session_state['user'], f"Eliminato utente web: {u['username']}")
                                    st.rerun()
                        else: 
                            st.info("Nessun utente web creato.")

                    with tab_audit:
                        st.markdown("### 📜 Registro Attività Studio")
                        st.caption("Registro accessi e modifiche ai dati (obbligo ex art. 30 GDPR).")
                        
                        ricerca_log = st.text_input("🔍 Cerca nel registro", placeholder="Es. Mario, Validazione...")
                        
                        try:
                            cursor.execute("SELECT data_log, utente_id, azione, documento_id FROM log_attivita WHERE studio_id = %s ORDER BY data_log DESC LIMIT 200", (studio_corrente,))
                            logs = cursor.fetchall()
                            if logs:
                                df_logs = pd.DataFrame(logs)
                                df_logs.columns = ["Data e Ora", "Operatore", "Azione Eseguita", "ID Doc"]
                                
                                if ricerca_log:
                                    df_logs = df_logs[df_logs.apply(lambda row: row.astype(str).str.contains(ricerca_log, case=False).any(), axis=1)]
                                
                                st.dataframe(df_logs, width="stretch", hide_index=True)
                            else:
                                st.info("Nessuna attività registrata.")
                        except Exception as e:
                            st.error(f"Errore caricamento log: {e}")
                    
                    
                    with tab_monitoraggio:
                        st.markdown("### 🚦 Semaforo Mensile Documenti")
                        st.caption("Verifica a colpo d'occhio quali aziende hanno già consegnato la contabilità e chi invece è in ritardo.")
                        
                        c_mese, c_anno = st.columns(2)
                        mesi = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
                        mese_oggi = datetime.date.today().month
                        anno_oggi = datetime.date.today().year
                        
                        sel_mese = c_mese.selectbox("Mese di competenza", mesi, index=mese_oggi-1)
                        sel_anno = c_anno.number_input("Anno", min_value=2020, max_value=2050, value=anno_oggi, step=1)
                        
                        mese_num = mesi.index(sel_mese) + 1
                        
                        # Query SQL: Incrocia tutte le aziende con i documenti del mese scelto
                        query_monitoraggio = """
                            SELECT a.nome AS azienda, COUNT(doc.id) AS conteggio
                            FROM aziende a
                            LEFT JOIN analisi doc ON a.nome = doc.azienda 
                                AND doc.studio_id = a.studio_id 
                                AND MONTH(doc.data_doc) = %s 
                                AND YEAR(doc.data_doc) = %s
                            WHERE a.studio_id = %s
                            GROUP BY a.nome
                            ORDER BY conteggio ASC, a.nome ASC
                        """
                        cursor.execute(query_monitoraggio, (mese_num, sel_anno, studio_corrente))
                        risultati = cursor.fetchall()
                        
                        if risultati:
                            # Intestazione colonne (più larghe per togliere il bottone)
                            h_sem, h_nome, h_docs = st.columns([1, 4, 3])
                            h_sem.markdown("**Stato**")
                            h_nome.markdown("**Azienda**")
                            h_docs.markdown("**Documenti Trovati**")
                            st.divider()

                            for r in risultati:
                                c_semaforo, c_nome, c_docs = st.columns([1, 4, 3], vertical_alignment="center")
                                
                                conteggio = r['conteggio']
                                azienda_nome = r['azienda']
                                
                                c_nome.markdown(f"**{azienda_nome}**")
                                
                                if conteggio == 0:
                                    c_semaforo.markdown("## 🔴")
                                    c_docs.error("Vuoto (Nessun documento)")
                                else:
                                    c_semaforo.markdown("## 🟢")
                                    c_docs.success(f"Ok ({conteggio} documenti)")
                                
                                st.markdown("<hr style='margin: 0px; opacity: 0.2;'>", unsafe_allow_html=True)
                        else:
                            st.info("Nessuna azienda registrata in questo studio.")
                    
                    conn.close()

        # ==========================================
        # VISTA 1: ADMIN PANEL
        # ==========================================
        elif st.session_state["view"] == "admin_panel" and is_admin:
            st.title("⚙️ Pannello Super Admin SaaS")
            col1, col2 = st.columns([1, 1.8])
            
            with col1:
                with st.container(border=True):
                    with st.form("form_nuovo_studio"):
                        st.subheader("Nuovo Studio SaaS")
                        rag_soc = st.text_input("Ragione Sociale Studio *")
                        id_studio = st.text_input("ID Studio Univoco *").lower()
                        piva_studio = st.text_input("Partita IVA Studio")
                        
                        c_scad, c_piano = st.columns([1, 1])
                        scadenza_studio = c_scad.date_input("Scadenza *", value=datetime.date.today() + datetime.timedelta(days=365), format="DD/MM/YYYY")
                        piano_selezionato = c_piano.selectbox("Piano SaaS", ["STARTER", "PRO", "BUSINESS"], index=1)
                        max_aziende = 9999 # Valore fisso altissimo per non dover modificare il database
                        
                        new_user = st.text_input("Username (Admin Studio) *").lower()
                        new_pass = st.text_input("Password *", type="password")
                        
                        if st.form_submit_button("Crea Account", width="stretch"):
                            if rag_soc and id_studio and new_user and new_pass:
                                hashed_pass = hash_password(new_pass)
                                conn = get_db_connection()
                                if conn:
                                    try:
                                        cursor = conn.cursor()
                                        cursor.execute("INSERT INTO studi_commercialisti (id_studio, ragione_sociale, partita_iva, scadenza_abbonamento, max_aziende, piano_tariffario) VALUES (%s, %s, %s, %s, %s, %s)", (sanitize_input(id_studio), sanitize_input(rag_soc), sanitize_input(piva_studio), scadenza_studio.strftime("%Y-%m-%d"), max_aziende, piano_selezionato))
                                        cursor.execute("INSERT INTO utenti (username, password, studio_id, ruolo) VALUES (%s, %s, %s, 'commercialista')", (sanitize_input(new_user), hashed_pass, sanitize_input(id_studio)))
                                        conn.commit()
                                        st.success("Studio creato!")
                                        time.sleep(1)
                                        st.rerun()
                                    except Exception as e:
                                        logging.error(f"Errore creazione admin: {e}")
                                        st.error("Impossibile creare lo studio. ID o Username già in uso.")
                                    finally:
                                        conn.close()
                    
                    st.divider()
                    with st.form("form_nuovo_operatore"):
                        st.subheader("Nuovo Operatore XML")
                        op_user = st.text_input("Username Operatore *").lower()
                        op_pass = st.text_input("Password *", type="password")
                        op_studio = st.text_input("ID Studio Associato *", value="studio_globale")
                        if st.form_submit_button("Crea Operatore XML", width="stretch"):
                            if op_user and op_pass:
                                conn = get_db_connection()
                                if conn:
                                    try:
                                        cursor = conn.cursor()
                                        cursor.execute("INSERT INTO utenti (username, password, studio_id, ruolo) VALUES (%s, %s, %s, 'operatore_xml')", (sanitize_input(op_user), hash_password(op_pass), sanitize_input(op_studio)))
                                        conn.commit()
                                        st.success("Operatore creato con successo!")
                                    except Exception as e:
                                        st.error("Errore: Username già in uso o database non pronto.")
                                    finally:
                                        conn.close()
                                        
            with col2:
                with st.container(border=True):
                    st.subheader("Monitoraggio SaaS")
                    tab1, tab2, tab3, tab4, tab5 = st.tabs(["🏢 Studi", "👥 Utenti Web", "🏭 Aziende", "📜 Log Globali", "💰 Fatturazione SaaS"])
                    conn = get_db_connection()
                    if conn:
                        cursor = conn.cursor(dictionary=True)
                        with tab1:
                            cursor.execute("SELECT * FROM studi_commercialisti")
                            studi_data = cursor.fetchall()
                            if studi_data:
                                ha, hb, hc, hd, he, hf = st.columns([3, 1.5, 2, 1.5, 0.5, 0.5])
                                ha.markdown("**Ragione Sociale**")
                                hb.markdown("**ID**")
                                hc.markdown("**P.IVA**")
                                hd.markdown("**Scadenza**")
                                st.divider()
                                
                                for s in studi_data:
                                    ca, cb, cc, cd, ce, cf = st.columns([3, 1.5, 2, 1.5, 0.5, 0.5])
                                    ca.write(s['ragione_sociale'])
                                    cb.write(s['id_studio'])
                                    cc.write(s['partita_iva'] or "-")
                                    
                                    scad_val = s.get('scadenza_abbonamento')
                                    if scad_val:
                                        scad_str = scad_val.strftime('%d/%m/%Y')
                                        if datetime.date.today() > scad_val:
                                            cd.error(scad_str)
                                        else:
                                            cd.write(scad_str)
                                    else:
                                        cd.write("Senza scadenza")
                                        
                                    with ce:
                                        with st.popover("✏️"):
                                            with st.form(key=f"edit_scad_{s['id_studio']}"):
                                                st.markdown(f"**Gestione Abbonamento**")
                                                nuova_scad = st.date_input("Nuova Scadenza", value=scad_val if scad_val else datetime.date.today() + datetime.timedelta(days=365), format="DD/MM/YYYY")
                                                # Manteniamo nel form il salvataggio automatico di 9999 per max_aziende per coerenza db
                                                if st.form_submit_button("Salva", type="primary", width="stretch"):
                                                    cursor.execute("UPDATE studi_commercialisti SET scadenza_abbonamento = %s, max_aziende = 9999 WHERE id_studio = %s", (nuova_scad.strftime("%Y-%m-%d"), s['id_studio']))
                                                    conn.commit()
                                                    st.rerun()

                                    if cf.button("❌", key=f"del_s_{s['id_studio']}"):
                                        cursor.execute("DELETE FROM studi_commercialisti WHERE id_studio=%s", (s['id_studio'],))
                                        cursor.execute("DELETE FROM utenti WHERE studio_id=%s AND ruolo!='admin'", (s['id_studio'],))
                                        cursor.execute("DELETE FROM aziende WHERE studio_id=%s", (s['id_studio'],))
                                        conn.commit()
                                        st.rerun()
                        with tab2:
                            cursor.execute("SELECT username, ruolo, studio_id, nome_azienda FROM utenti")
                            utenti_data = cursor.fetchall()
                            if utenti_data:
                                hu, hr, hs, ha, hact = st.columns([2.5, 2, 2, 2.5, 1])
                                hu.markdown("**User**")
                                hr.markdown("**Ruolo**")
                                hs.markdown("**Studio**")
                                ha.markdown("**Azienda**")
                                st.divider()
                                for u in utenti_data:
                                    cu, cr, cs, ca, cact = st.columns([2.5, 2, 2, 2.5, 1])
                                    cu.write(u['username'])
                                    cr.write("👑" if u['ruolo']=='admin' else "💼" if u['ruolo']=='commercialista' else "🌐")
                                    cs.write(u['studio_id'])
                                    ca.write(u['nome_azienda'] or "-")
                                    if u['username'] != st.session_state['user']:
                                        if cact.button("❌", key=f"del_u_{u['username']}"): 
                                            cursor.execute("DELETE FROM utenti WHERE username=%s", (u['username'],))
                                            conn.commit()
                                            st.rerun()
                        with tab3:
                            cursor.execute("SELECT * FROM aziende")
                            aziende_data = cursor.fetchall()
                            if aziende_data:
                                ha, hp, hs, hact1, hact2 = st.columns([3, 2, 2, 0.5, 0.5])
                                ha.markdown("**Azienda**")
                                hp.markdown("**P.IVA**")
                                hs.markdown("**Studio**")
                                st.divider()
                                
                                for az in aziende_data:
                                    ca, cp, cs, cact1, cact2 = st.columns([3, 2, 2, 0.5, 0.5])
                                    ca.write(az['nome'])
                                    cp.write(az['partita_iva'] or "-")
                                    cs.write(az['studio_id'])
                                    
                                    with cact1:
                                        with st.popover("✏️"):
                                            with st.form(key=f"edit_az_adm_{az['nome']}_{az['studio_id']}"):
                                                st.markdown(f"**Modifica {az['nome']} (Studio: {az['studio_id']})**")
                                                e_nome = st.text_input("Ragione Sociale *", value=az['nome'])
                                                e_piva = st.text_input("Partita IVA *", value=az['partita_iva'] or "")
                                                e_cf = st.text_input("Codice Fiscale", value=az['codice_fiscale'] or "")
                                                e_ind = st.text_input("Indirizzo", value=az.get('indirizzo', '') or "")
                                                e_cap = st.text_input("CAP", value=az.get('cap', '') or "")
                                                e_citta = st.text_input("Città", value=az.get('citta', '') or "")
                                                e_prov = st.text_input("Provincia", value=az.get('provincia', '') or "")
                                                
                                                if st.form_submit_button("Salva", type="primary", width="stretch"):
                                                    if e_nome and e_piva:
                                                        cursor.execute("UPDATE aziende SET nome=%s, partita_iva=%s, codice_fiscale=%s, indirizzo=%s, cap=%s, citta=%s, provincia=%s WHERE nome=%s AND studio_id=%s", (sanitize_input(e_nome), sanitize_input(e_piva), sanitize_input(e_cf), sanitize_input(e_ind), sanitize_input(e_cap), sanitize_input(e_citta), sanitize_input(e_prov), az['nome'], az['studio_id']))
                                                        if sanitize_input(e_nome) != az['nome']:
                                                            cursor.execute("UPDATE utenti SET nome_azienda=%s WHERE nome_azienda=%s AND studio_id=%s", (sanitize_input(e_nome), az['nome'], az['studio_id']))
                                                            cursor.execute("UPDATE analisi SET azienda=%s WHERE azienda=%s AND studio_id=%s", (sanitize_input(e_nome), az['nome'], az['studio_id']))
                                                        conn.commit()
                                                        st.rerun()
                                                    else:
                                                        st.error("Dati mancanti!")
                                                        
                                    with cact2:
                                        if st.button("❌", key=f"del_az_{az['nome']}_{az['studio_id']}"):
                                            cursor.execute("DELETE FROM aziende WHERE nome=%s AND studio_id=%s", (az['nome'], az['studio_id']))
                                            cursor.execute("DELETE FROM utenti WHERE nome_azienda=%s AND studio_id=%s", (az['nome'], az['studio_id']))
                                            cursor.execute("DELETE FROM analisi WHERE azienda=%s AND studio_id=%s", (az['nome'], az['studio_id']))
                                            conn.commit()
                                            st.rerun()

                        with tab4:
                            st.markdown("### 📜 Audit Log Globale di Sistema")
                            try:
                                cursor.execute("SELECT data_log, studio_id, utente_id, azione, documento_id FROM log_attivita ORDER BY data_log DESC LIMIT 500")
                                all_logs = cursor.fetchall()
                                if all_logs:
                                    df_all = pd.DataFrame(all_logs)
                                    df_all.columns = ["Data e Ora", "Studio ID", "Operatore", "Azione Eseguita", "ID Doc"]
                                    st.dataframe(df_all, width="stretch", hide_index=True)
                                else:
                                    st.info("Nessuna attività globale registrata.")
                            except Exception as e:
                                st.error(f"Errore caricamento log: {e}")
                        
                        with tab5:
                            st.markdown("### 💰 Report Fatturazione SaaS")
                            st.caption("Visualizza i consumi aggregati per emettere le fatture agli studi commercialisti.")
                            
                            # Filtro mese per l'admin (di default mostra il mese corrente, ma puoi scrivere quello passato)
                            mese_admin = st.text_input("Mese di riferimento (Formato: YYYY-MM)", value=datetime.date.today().strftime("%Y-%m"))
                            
                            cursor.execute("""
                                SELECT 
                                    c.studio_id, 
                                    s.ragione_sociale, 
                                    s.piano_tariffario, 
                                    SUM(c.documenti_processati) as tot_doc, 
                                    SUM(c.pagine_processate) as tot_pag
                                FROM consumi_ai c
                                JOIN studi_commercialisti s ON c.studio_id = s.id_studio
                                WHERE c.mese_anno = %s
                                GROUP BY c.studio_id, s.ragione_sociale, s.piano_tariffario
                            """, (mese_admin,))
                            
                            dati_fatturazione = cursor.fetchall()
                            
                            if dati_fatturazione:
                                report_list = []
                                for d_fatt in dati_fatturazione:
                                    piano_studio = d_fatt.get('piano_tariffario', 'PRO')
                                    docs = int(d_fatt['tot_doc'])
                                    base_euro, soglia_doc, estr_doc, cst_extra, tot_fatturare = calcola_fatturazione_studio(docs, piano_studio)
                                    
                                    report_list.append({
                                        "Studio": d_fatt['ragione_sociale'],
                                        "ID": d_fatt['studio_id'],
                                        "Piano": piano_studio,
                                        "Doc. Estratti": docs,
                                        "Quota Base": f"€ {base_euro:.2f}",
                                        "Spesa Extra": f"€ {cst_extra:.2f}",
                                        "TOTALE DA FATTURARE": f"€ {tot_fatturare:.2f}"
                                    })
                                
                                df_report = pd.DataFrame(report_list)
                                st.dataframe(df_report, width="stretch", hide_index=True)
                                
                                # Calcolo incasso totale previsto per quel mese
                                tot_incasso = sum(float(r["TOTALE DA FATTURARE"].replace("€ ", "")) for r in report_list)
                                st.success(f"**💶 Previsione Incasso per {mese_admin}: € {tot_incasso:.2f}**")
                            else:
                                st.info(f"Nessun consumo registrato per il mese {mese_admin}.")
                        
                        
                        conn.close()

        # ==========================================
        # VISTA 2: VALIDAZIONE HUMAN-IN-THE-LOOP 
        # ==========================================
        elif st.session_state["view"] == "detail":
            d = st.session_state["doc_attivo"]
            
            stato_attuale = d.get("stato", "caricato")
            col_titolo, col_badge = st.columns([3, 1])
            
            if is_cliente: 
                col_titolo.title("👁️ Verifica Documento")
            else: 
                col_titolo.title("🔍 Revisione Documento")
            
            if stato_attuale in ["errore_ai"]: 
                col_badge.error("❌ Errore AI (Da  correggere)")
            elif stato_attuale in ["caricato", "in_pre_validazione", "Da verificare"]: 
                col_badge.warning("🟡 Da verificare" if is_cliente else "🟡 Attesa check cliente")
            elif stato_attuale == "inviato_per_validazione": 
                col_badge.info("🔵 Inviato allo Studio" if is_cliente else "🔵 Da Validare")
            elif stato_attuale == "analizzato": 
                col_badge.success("🔵 Inviato allo Studio" if is_cliente else "🟢 Pronto (Da Validare)")
            elif stato_attuale in ["validato", "Confermato"]: 
                col_badge.success("✅ Registrato" if is_cliente else "✅ Validato (Umano)")
                
            st.divider()
            col_pdf, col_form = st.columns([1.2, 1])
            
            with col_pdf:
                with st.container(border=True):
                    st.subheader("📄 Documento Originale")
                    if d.get("file_path") and os.path.exists(d.get("file_path", "")):
                        try:
                            if d["file_path"].lower().endswith('.pdf'):
                                with fitz.open(d["file_path"]) as pdf_doc:
                                    page = pdf_doc.load_page(0)
                                    mat = fitz.Matrix(2, 2)
                                    pix = page.get_pixmap(matrix=mat)
                                    img_preview = Image.open(io.BytesIO(pix.tobytes("png")))
                                    st.image(img_preview, width="stretch")
                            else:
                                img_preview = Image.open(d["file_path"])
                                st.image(img_preview, width="stretch")
                        except Exception as e:
                            logging.error(f"Errore visualizzazione file in detail ({d.get('file_path')}): {e}")
                            st.info("Anteprima sicura non renderizzabile. Utilizza il download sicuro qui sotto per visionare il file.")
                        
                        st.markdown("<br>", unsafe_allow_html=True)
                        with open(d["file_path"], "rb") as file:
                            st.download_button("⬇️ Scarica File Originale", data=file, file_name=os.path.basename(d["file_path"]), type="secondary")
                    else:
                        st.warning("Archivio disconnesso per questo elemento.")

            with col_form:
                with st.container(border=True):
                    st.subheader("✍️ Dati Estratti" if is_cliente else "✍️ Revisione")
                    
                    with st.form("form_validazione"):
                        
                        if stato_attuale == "errore_ai":
                            st.error("⚠️ Il processo AI non è andato a buon fine. Procedere con input manuale del documento.")
                        else:
                            st.markdown(f"**Descrizione AI:** {d.get('descrizione', '')}")
                        
                        try: 
                            score_attuale = int(float(str(d.get('confidence_score', 0)).replace('%', '').strip()))
                        except: 
                            score_attuale = 0
                        
                        if score_attuale >= 95: 
                            st.success(f"🟢 **Affidabilità AI ({score_attuale}%) - ✔ Pre-validato**")
                        elif score_attuale >= 90: 
                            st.success(f"🟢 **Affidabilità AI ({score_attuale}%)**")
                        elif score_attuale >= 70: 
                            st.warning(f"🟡 **Affidabilità media ({score_attuale}%)** - Verifica i dati.")
                        elif stato_attuale != "errore_ai": 
                            st.error(f"🔴 **Bassa affidabilità ({score_attuale}%)** - Revisione attenta consigliata.")
                        
                        # --- INIZIALIZZAZIONE VARIABILI DI SICUREZZA E CONTROLLO SEGNO ---
                        f_ritenuta = float(d.get('ritenuta_acconto', 0.0))
                        f_tot_iniziale = float(d.get('totale', 0.0))
                        
                        st.markdown("### Dati Base")
                        
                        # --- AUTOCLASSIFICAZIONE INTELLIGENTE ESTERO ---
                        tipo_corrente = d.get('tipo_documento', 'FATTURA')
                        flag_estero_iniziale = bool(d.get('flag_estero', False))
                        
                        # Se è estera, forziamo la classificazione escludendo le Autofatture e le Note di Credito
                        if flag_estero_iniziale and tipo_corrente not in ["AUTOFATTURA", "NOTA_CREDITO", "PRESTAZIONE_OCCASIONALE", "DOCUMENTO_COMMERCIALE"]:
                            tipo_corrente = "FATTURA_ESTERA"
                            
                        lista_tipi_doc = ["FATTURA", "NOTA_CREDITO", "AUTOFATTURA", "PRESTAZIONE_OCCASIONALE", "DOCUMENTO_COMMERCIALE", "FATTURA_ESTERA", "ALTRO"]
                        if tipo_corrente not in lista_tipi_doc:
                            tipo_corrente = "FATTURA"
                            
                        # --- NUOVO: Selezione tipo documento normalizzata e flag estero ---
                        c_tipo, c_est = st.columns([2, 1])
                        f_tipo_doc = c_tipo.selectbox("Classificazione Fiscale", lista_tipi_doc, index=lista_tipi_doc.index(tipo_corrente), disabled=is_cliente)
                        
                        
                        st.markdown("<br>", unsafe_allow_html=True)
                        f_fornitore = st.text_input("Controparte (Ragione Sociale o Nome)", value=d.get('fornitore', '') if d.get('fornitore') != 'ERRORE IA' else '')
                        
                        c_doc, c_data, c_scad = st.columns(3)
                        f_num = c_doc.text_input("Numero Doc.", value=d.get('numero_fattura', ''), disabled=is_cliente) 
                        
                        data_iniziale = pd.to_datetime(d.get('data_doc', ''), format='mixed', errors='coerce')
                        f_data = c_data.date_input("Emissione", value=data_iniziale.date() if pd.notnull(data_iniziale) else datetime.date.today(), format="DD/MM/YYYY")

                        data_scadenza_iniziale = pd.to_datetime(d.get('data_scadenza', ''), format='mixed', errors='coerce')
                        f_scadenza = c_scad.date_input("Scadenza", value=data_scadenza_iniziale.date() if pd.notnull(data_scadenza_iniziale) else f_data, format="DD/MM/YYYY")

                        f_tot = st.number_input("Totale Documento (€)", value=f_tot_iniziale, format="%.2f")
                        
                        if is_cliente:
                            f_note = st.text_area("Vuoi dire qualcosa al commercialista su questo documento?", value=d.get('note_cliente', '') or '', placeholder="Es: Pagato con carta personale...")
                        else:
                            f_note = st.text_area("Note del Cliente", value=d.get('note_cliente', '') or '', disabled=True)
                        
                        if not is_cliente:
                            st.divider()
                            st.markdown("### 🏛️ Dati Fiscali Avanzati")
                            c_piva, c_cf = st.columns(2)
                            f_piva = c_piva.text_input("Partita IVA", value=d.get('piva', ''))
                            f_cf = c_cf.text_input("Codice Fiscale", value=d.get('codice_fiscale', ''))
                            
                            c_chk1, c_chk2 = st.columns(2)
                            f_flag_estero = c_chk1.checkbox("Fornitore/Cliente Estero (No P.IVA IT)", value=bool(d.get('flag_estero', False)))
                            
                            # --- AUTOMATISMO REVERSE CHARGE ---
                            # Si attiva da solo se l'IA o tu classificate come AUTOFATTURA o FATTURA_ESTERA
                            default_xml = True if f_tipo_doc in ["AUTOFATTURA", "FATTURA_ESTERA"] else str_to_bool(d.get('richiede_xml', False))
                            f_richiede_xml = c_chk2.checkbox("Richiede Integrazione IVA (Reverse Charge)", value=default_xml)
                            
                            mappe = get_mappature(studio_corrente)
                            cat_ia = d.get('categoria_contabile', '')
                            conto_suggerito = mappe.get(cat_ia, {}).get('codice_conto', '')
                            
                            # --- LOGICA WORKFLOW INTELLIGENTE (UI DINAMICA) ---
                            # Forza l'apparizione del messaggio Giallo in UI se i tipi doc lo richiedono
                            is_reverse_charge = f_tipo_doc in ["AUTOFATTURA", "FATTURA_ESTERA"] or f_richiede_xml
                            
                            # Determiniamo quali campi fiscali mostrare
                            mostra_iva = f_tipo_doc != "PRESTAZIONE_OCCASIONALE"
                            mostra_ritenuta = f_tipo_doc in ["PRESTAZIONE_OCCASIONALE", "ALTRO"]
                            
                            f_iva = 0.0
                            
                            # Creiamo le colonne dinamicamente
                            col_sizes = [1.5, 1]
                            if mostra_iva: col_sizes.append(1)
                            if mostra_ritenuta: col_sizes.append(1)
                            
                            cols_fiscali = st.columns(col_sizes)
                            
                            # Generati una sola volta: niente più DuplicateElementId!
                            f_cat = cols_fiscali[0].text_input("Categoria Merceologica", value=cat_ia)
                            f_conto = cols_fiscali[1].text_input("Codice Conto", value=d.get('codice_conto', conto_suggerito) or conto_suggerito)
                            
                            idx_col = 2
                            if mostra_iva:
                                label_iva = "IVA integrata (€)" if is_reverse_charge else "Imposta IVA (€)"
                                f_iva = cols_fiscali[idx_col].number_input(label_iva, value=float(d.get('iva_euro', 0.0)), format="%.2f")
                                idx_col += 1
                            
                            if mostra_ritenuta:
                                f_ritenuta = cols_fiscali[idx_col].number_input("Ritenuta d'Acconto (€)", value=f_ritenuta, format="%.2f")
                            else:
                                f_ritenuta = 0.0 
                                
                            # Messaggi contestuali
                            if f_tipo_doc == "PRESTAZIONE_OCCASIONALE":
                                st.info("💡 **Prestazione Occasionale:** Il campo 'Totale Documento' rappresenta il Compenso Lordo. Non è prevista IVA.")
                            elif is_reverse_charge:
                                st.markdown("### 🔄 Reverse Charge Rilevato")
                                st.warning("Questo documento estero richiede l'integrazione dell'IVA per la registrazione in contabilità.")

                        st.markdown("<br>", unsafe_allow_html=True)
                        
                        # --- FIX BOTTONI: Chiamata diretta per accontentare il parser di Streamlit ---
                        c_btn1, c_btn2 = st.columns(2)
                        
                        lbl_btn = "Conferma e Invia allo Studio 🚀" if is_cliente else "✅ Valida"
                        style_btn = "primary" if is_cliente else "secondary"
                        
                        # Il bottone principale è sempre visibile e chiaro nel codice
                        submitted = c_btn1.form_submit_button(lbl_btn, type=style_btn, width="stretch")
                        
                        submitted_next = False
                        if not is_cliente:
                            submitted_next = c_btn2.form_submit_button("⏭️ Valida e Apri Prossimo", type="primary", width="stretch")
                            
                        if submitted or submitted_next:
                            f_fornitore = sanitize_input(f_fornitore)
                            f_note = sanitize_input(f_note)
                            f_tipo_doc_save = f_tipo_doc if not is_cliente else d.get('tipo_documento', 'FATTURA')
                            
                            if not is_cliente:
                                # --- FORZATURA FISCALE ESTERO (Controllo finale) ---
                                if f_flag_estero and f_tipo_doc_save not in ["AUTOFATTURA", "NOTA_CREDITO", "PRESTAZIONE_OCCASIONALE", "DOCUMENTO_COMMERCIALE"]:
                                    f_tipo_doc_save = "FATTURA_ESTERA"
                                    
                                # --- BLOCCO SICUREZZA DATABASE ---
                                f_num = sanitize_input(f_num)[:50]
                                f_piva = pulisci_codice_fiscale_piva(f_piva)
                                f_cf = pulisci_codice_fiscale_piva(f_cf)
                                f_cat = sanitize_input(f_cat)[:100]
                                f_conto = sanitize_input(f_conto)[:50]
                                
                                # --- NORMALIZZAZIONE VALORI IN ASSOLUTO ---
                                # Le Note di Credito vanno salvate in positivo. La causale (es. NC-V) farà lo storno nel gestionale.
                                f_tot = abs(f_tot)
                                f_iva = abs(f_iva)
                                f_ritenuta = abs(f_ritenuta)
                                
                            conn = get_db_connection()
                            if conn:
                                cursor = conn.cursor()
                                if is_cliente:
                                    cursor.execute("""
                                        UPDATE analisi 
                                        SET fornitore=%s, data_doc=%s, data_scadenza=%s, totale=%s, note_cliente=%s, stato='inviato_per_validazione' 
                                        WHERE id=%s AND studio_id=%s
                                    """, (f_fornitore, f_data.strftime("%Y-%m-%d"), f_scadenza.strftime("%Y-%m-%d"), f_tot, f_note, d['id'], studio_corrente))
                                    log_action(studio_corrente, st.session_state['user'], "Pre-validazione Cliente", d['id'])
                                else:
                                    cursor.execute("""
                                        UPDATE analisi 
                                        SET fornitore=%s, piva=%s, codice_fiscale=%s, numero_fattura=%s, data_doc=%s, data_scadenza=%s, totale=%s, iva_euro=%s, categoria_contabile=%s, codice_conto=%s, tipo_documento=%s, ritenuta_acconto=%s, flag_estero=%s, richiede_xml=%s, stato='validato', richiede_verifica=0 
                                        WHERE id=%s AND studio_id=%s
                                    """, (f_fornitore, f_piva, f_cf, f_num, f_data.strftime("%Y-%m-%d"), f_scadenza.strftime("%Y-%m-%d"), f_tot, f_iva, f_cat, f_conto, f_tipo_doc_save, f_ritenuta, f_flag_estero, f_richiede_xml, d['id'], studio_corrente))
                                    log_action(studio_corrente, st.session_state['user'], "Validazione Studio", d['id'])
                                    msg = "Documento validato!"

                                conn.commit()

                                if submitted_next and not is_cliente:
                                    cursor = conn.cursor(dictionary=True)
                                    cursor.execute("""
                                        SELECT * FROM analisi 
                                        WHERE studio_id = %s AND azienda = %s AND stato != 'validato' AND id != %s
                                        ORDER BY 
                                            CASE WHEN stato = 'errore_ai' THEN 1 WHEN confidence_score < 70 THEN 2 ELSE 3 END,
                                            confidence_score ASC, data_inserimento DESC
                                        LIMIT 1
                                    """, (studio_corrente, d['azienda'], d['id']))
                                    next_doc = cursor.fetchone()
                                    
                                    if next_doc:
                                        st.session_state["doc_attivo"] = next_doc
                                        st.success("Salvato! Caricamento prossimo documento...")
                                    else:
                                        st.session_state["view"] = "main"
                                        st.session_state["main_tab"] = "📊 CRUSCOTTO E LAVORO"
                                        st.success("Hai finito! Non ci sono più documenti da validare per questa azienda.")
                                else:
                                    if is_cliente: 
                                        st.session_state["view"] = "main"
                                    else: 
                                        st.session_state["doc_attivo"]["stato"] = "validato"
                                        st.success(msg)

                                conn.close()
                                time.sleep(0.5)
                                st.rerun()

                st.markdown("<br>", unsafe_allow_html=True)
                col_act1, col_act2, col_act3 = st.columns([1, 1.5, 1])
                if col_act1.button("⬅️ Torna Indietro", width="stretch"): 
                    st.session_state["view"] = "main"
                    st.session_state["main_tab"] = "📊 CRUSCOTTO E LAVORO"
                    st.rerun()
                
                # --- LOGICA ESPORTAZIONE XML (VERSIONE BOZZA) ---
                # Il bottone XML deve comparire per le fatture attive (ENTRATA) e per quelle passive che richiedono integrazione (USCITA con richiede_xml = true)
                is_xml_richiesto = str_to_bool(d.get('richiede_xml', False))
                mostra_btn_xml = not is_cliente and (d.get('direzione') == 'ENTRATA' or is_xml_richiesto)
                
                if mostra_btn_xml:
                    if stato_attuale in ["validato", "Confermato"]:
                        info_az = get_info_azienda(st.session_state["selected_azienda"], studio_corrente)
                        xml_out = genera_xml_fatturapa(d, info_az)
                        # Pulsante attivo: rinominato in "Bozza" per sicurezza
                        if col_act2.download_button("⚡ Genera Bozza XML", data=xml_out, file_name=f"Bozza_Fattura_{d['id']}.xml", mime="application/xml", width="stretch"):
                            log_action(studio_corrente, st.session_state['user'], "Generata Bozza XML", d['id'])
                    else: 
                        # Pulsante disabilitato: allineato nel nome
                        col_act2.button("⚡ Genera Bozza XML", disabled=True, help="Valida il documento prima di generare la bozza", width="stretch")
                        
                if not is_cliente:
                    if col_act3.button("🗑️ Elimina Documento", width="stretch"):
                        conn = get_db_connection()
                        if conn:
                            cursor = conn.cursor()
                            cursor.execute("DELETE FROM analisi WHERE id = %s AND studio_id = %s", (d['id'], studio_corrente))
                            if cursor.rowcount > 0:
                                conn.commit()
                                log_action(studio_corrente, st.session_state['user'], "Eliminato Documento", d['id'])
                                try:
                                    if d.get("file_path") and os.path.exists(d["file_path"]): 
                                        os.remove(d["file_path"])
                                except Exception as fs_err:
                                    logging.error(f"Errore cancellazione file fisico: {fs_err}")
                            conn.close()
                            st.session_state["view"] = "main"
                            st.session_state["main_tab"] = "📊 CRUSCOTTO E LAVORO"
                            st.rerun()

        # ==========================================
        # VISTA 3: DASHBOARD PRINCIPALE E UPLOAD
        # ==========================================
        elif st.session_state["view"] == "main":
            lista_aziende = get_aziende(studio_corrente)
            
            if is_cliente:
                azienda_attiva = st.session_state["nome_azienda"]
                st.session_state["selected_azienda"] = azienda_attiva
            else:
                
                # --- ALERT SCADENZA ABBONAMENTO ---
                if is_commercialista and st.session_state.get("scadenza_abbonamento"):
                    scadenza = st.session_state["scadenza_abbonamento"]
                    giorni_rimanenti = (scadenza - datetime.date.today()).days
                    
                    if giorni_rimanenti == 0:
                        st.error("🚨 ATTENZIONE: Il tuo abbonamento scade OGGI! Contatta l'amministratore per il rinnovo ed evitare l'imminente blocco degli accessi.")
                    elif 0 < giorni_rimanenti <= 3:
                        st.warning(f"⚠️ ATTENZIONE: Il tuo abbonamento scadrà tra {giorni_rimanenti} giorni ({scadenza.strftime('%d/%m/%Y')}). Contatta l'amministratore per il rinnovo.")

                with st.container(border=True):
                    st.subheader("🏢 Area di Lavoro Studio")
                    col_sel, col_add, col_acc = st.columns([2, 1, 1])
                    with col_sel:
                        opzioni_aziende = ["--- Scegli Azienda ---"] + lista_aziende
                        if st.session_state["selected_azienda"] not in opzioni_aziende: 
                            st.session_state["selected_azienda"] = opzioni_aziende[0]
                        idx_corrente = opzioni_aziende.index(st.session_state["selected_azienda"])
                        nuova_scelta = st.selectbox("Seleziona Azienda Gestita", opzioni_aziende, index=idx_corrente, label_visibility="collapsed")
                        if nuova_scelta != st.session_state["selected_azienda"]: 
                            st.session_state["selected_azienda"] = nuova_scelta
                            st.rerun()
                        azienda_attiva = st.session_state["selected_azienda"]
                    with col_add:
                        # 1. Svuotiamo i campi PRIMA di disegnarli se il salvataggio precedente è andato a buon fine
                        if st.session_state.get("svuota_form_azienda", False):
                            for k in ["n_az_nome", "n_az_piva", "n_az_cf", "n_az_ind", "n_az_cap", "n_az_cit", "n_az_prv"]:
                                st.session_state[k] = ""
                            st.session_state["svuota_form_azienda"] = False

                        # 2. Inizializziamo la memoria dei campi se non esiste ancora
                        for k in ["n_az_nome", "n_az_piva", "n_az_cf", "n_az_ind", "n_az_cap", "n_az_cit", "n_az_prv"]:
                            if k not in st.session_state:
                                st.session_state[k] = ""

                        with st.popover("➕ Crea Nuova Azienda", width="stretch"):
                            st.markdown("**Anagrafica Azienda**")
                            
                            n_nome = st.text_input("Ragione Sociale *", key="n_az_nome")
                            n_piva = st.text_input("Partita IVA *", key="n_az_piva")
                            n_cf = st.text_input("Codice Fiscale", key="n_az_cf")
                            n_ind = st.text_input("Indirizzo", key="n_az_ind")
                            n_cap = st.text_input("CAP", key="n_az_cap")
                            n_citta = st.text_input("Città", key="n_az_cit")
                            n_prov = st.text_input("Provincia", key="n_az_prv")
                            
                            if st.button("Salva Azienda", width="stretch"):
                                if n_nome and n_piva:
                                    if n_nome not in lista_aziende: 
                                        add_azienda(n_nome, n_piva, n_cf, n_ind, n_cap, n_citta, n_prov, studio_corrente)
                                        st.session_state["selected_azienda"] = sanitize_input(n_nome)
                                        st.success("Azienda creata!")
                                        st.session_state["svuota_form_azienda"] = True
                                        time.sleep(1)
                                        st.rerun()
                                    else: 
                                        st.warning("Azienda già esistente in questo studio.")
                                else: 
                                    st.warning("Ragione Sociale e P.IVA obbligatori!")
                    with col_acc:
                        if azienda_attiva != "--- Scegli Azienda ---":
                            with st.popover("🔑 Accesso Cliente", width="stretch"):
                                st.markdown(f"**Crea accesso per {azienda_attiva}**")
                                user_az = st.text_input("Username").lower()
                                pass_az = st.text_input("Password", type="password")
                                if st.button("Genera Accesso", width="stretch"):
                                    if user_az and pass_az:
                                        conn = get_db_connection()
                                        if conn:
                                            try:
                                                cursor = conn.cursor()
                                                cursor.execute("INSERT INTO utenti (username, password, studio_id, ruolo, nome_azienda) VALUES (%s, %s, %s, 'cliente', %s)", (sanitize_input(user_az), hash_password(pass_az), studio_corrente, azienda_attiva))
                                                conn.commit()
                                                st.success("Credenziali create!")
                                                log_action(studio_corrente, st.session_state['user'], f"Creato utente web per {azienda_attiva}")
                                            except Exception as create_user_err: 
                                                logging.error(f"Errore creazione utente db: {create_user_err}")
                                                st.error("Username non disponibile o errore di salvataggio.")
                                            finally:
                                                conn.close()

            if azienda_attiva == "--- Scegli Azienda ---":
                st.info("👈 Seleziona un'azienda nel pannello in alto per accedere alla documentazione.")
            else:
                info_az = get_info_azienda(azienda_attiva, studio_corrente)
                
                # --- 1. PRE-FETCH DEI DATI (INCAPSULATO PER EVITARE GHOSTING) ---
                def load_dati_azienda():
                    df = pd.DataFrame()
                    storico = []
                    conn = get_db_connection()
                    if conn:
                        cursor = conn.cursor(dictionary=True) 
                        cursor.execute("""
                            SELECT * FROM analisi 
                            WHERE studio_id = %s AND azienda = %s 
                            ORDER BY 
                                CASE WHEN stato = 'errore_ai' THEN 1 WHEN confidence_score < 70 THEN 2 ELSE 3 END,
                                confidence_score ASC, data_inserimento DESC
                        """, (studio_corrente, azienda_attiva))
                        storico = cursor.fetchall()
                        conn.close()

                    if storico:
                        df = pd.DataFrame(storico)
                        
                        def formatta_zeri_fiscali(val):
                            if pd.isnull(val): return ""
                            v = str(val).strip()
                            if v.endswith('.0'): v = v[:-2]
                            if v.lower() in ['none', 'nan', 'null', '']: return ""
                            if v.isdigit() and 0 < len(v) < 11: return v.zfill(11)
                            return v
                            
                        if 'piva' in df.columns:
                            df['piva'] = df['piva'].apply(formatta_zeri_fiscali)
                        if 'codice_fiscale' in df.columns:
                            df['codice_fiscale'] = df['codice_fiscale'].apply(formatta_zeri_fiscali)
                        if 'numero_fattura' in df.columns:
                            df['numero_fattura'] = df['numero_fattura'].apply(
                                lambda x: str(x)[:-2] if str(x).endswith('.0') else str(x)
                            ).replace(['None', 'nan', 'null'], '')

                        df['totale_num'] = pd.to_numeric(df['totale'], errors='coerce').fillna(0)
                        df['iva_euro_num'] = pd.to_numeric(df['iva_euro'], errors='coerce').fillna(0)
                        df['confidence_score'] = pd.to_numeric(df.get('confidence_score', 0), errors='coerce').fillna(0)
                        df['data_parsed'] = pd.to_datetime(df['data_doc'], format='mixed', errors='coerce')
                        df['data_formattata'] = df['data_parsed'].dt.strftime('%d/%m/%Y').fillna(df['data_doc'])
                    return df, storico

                # --- 2. LOGICA UPLOAD CENTRALIZZATA ---
                def render_upload_box():
                    with st.container(border=True):
                        if is_cliente:
                            st.markdown("##### 📤 Invia documenti allo studio")
                            st.caption("Clicca qui sotto per caricare i tuoi PDF o scattare una foto al documento.")
                        else:
                            st.markdown("#### 📤 Upload")
                            st.caption("Trascina qui tutti i documenti. AI Doc Studio effettuerà l'estrazione e la categorizzazione automatica.")

                        if "uploader_key" not in st.session_state: 
                            st.session_state["uploader_key"] = 0
                            
                        tab_up, tab_cam = st.tabs(["📂 Carica documenti", "📸 Scatta foto"])
                        
                        with tab_up:
                            files_upload = st.file_uploader("Trascina PDF o Immagini", type=["pdf","jpg","png","jpeg"], accept_multiple_files=True, key=f"up_{st.session_state['uploader_key']}", label_visibility="collapsed")
                            btn_text = "🚀 Invia Documenti" if is_cliente else "🚀 Processa con AI Doc Studio"
                            
                            if files_upload:
                                # Rimosso width="stretch", il bottone riprende la dimensione standard
                                invia_files = st.button(btn_text, type="primary", key="btn_invia_files")
                            else:
                                invia_files = False
                            
                        with tab_cam:
                            foto_cam = st.camera_input("Scatta al documento", label_visibility="collapsed")
                            
                            if foto_cam:
                                # Rimosso width="stretch"
                                invia_foto = st.button("🚀 Processa con AI Doc Studio", type="primary", key="btn_invia_foto")
                            else:
                                invia_foto = False
                            
                        # Determiniamo quale file processare unificando la logica per il blocco successivo
                        files = []
                        avvia_processo = False
                        
                        if invia_files and files_upload:
                            files = files_upload
                            avvia_processo = True
                        elif invia_foto and foto_cam:
                            files = [foto_cam] 
                            avvia_processo = True
                            
                        if avvia_processo:
                            # FORZATURA UI: Mostra la rotellina di caricamento durante il salvataggio
                            with st.spinner("⏳ Messa in coda dei documenti in corso..."):
                                batch_id = str(uuid.uuid4())
                                utente_corrente = st.session_state['user']
                                
                                # --- NOVITÀ: Salviamo in memoria TUTTI i caricamenti attivi ---
                                if "active_batches_queue" not in st.session_state:
                                    st.session_state["active_batches_queue"] = []
                                st.session_state["active_batches_queue"].append(batch_id)
                                
                                conn = get_db_connection()
                                if conn:
                                    cursor = conn.cursor()
                                    save_dir = os.path.join(UPLOAD_DIR, studio_corrente, azienda_attiva.replace(" ", "_"), "QUEUE")
                                    os.makedirs(save_dir, exist_ok=True)
                                    
                                    for f in files:
                                        original_name = getattr(f, 'name', 'foto.jpg')
                                        safe_name = sanitize_filename(original_name)
                                        file_path = os.path.join(save_dir, f"{uuid.uuid4().hex}_{safe_name}")
                                        
                                        if hasattr(f, 'getvalue'):
                                            with open(file_path, "wb") as out_f: out_f.write(f.getvalue())
                                        else:
                                            with open(file_path, "wb") as out_f: out_f.write(f.read())
                                            
                                        cursor.execute("""
                                            INSERT INTO processing_queue (batch_id, studio_id, utente_id, azienda, file_path, nome_originale, stato)
                                            VALUES (%s, %s, %s, %s, %s, %s, 'in_coda')
                                        """, (batch_id, studio_corrente, utente_corrente, azienda_attiva, file_path, original_name))
                                    
                                    conn.commit()
                                    conn.close()
                                    
                                    # Riduciamo il tempo di pausa, il feedback visivo dello spinner è sufficiente
                                    time.sleep(0.5) 
                                    st.session_state["uploader_key"] += 1
                                    st.rerun()

                        # =========================================================
                        # BARRA DI PROGRESSIONE (DENTRO L'UPLOAD BOX)
                        # =========================================================
                        @st.fragment(run_every="3s")
                        def auto_refresh_coda_interna():
                            conn = get_db_connection()
                            if conn:
                                cursor = conn.cursor(dictionary=True)
                                
                                # 1. Cerchiamo SOLO i batch realmente attivi nel DB (ignoriamo i vecchi completati)
                                cursor.execute("""
                                    SELECT DISTINCT batch_id 
                                    FROM processing_queue 
                                    WHERE studio_id = %s AND azienda = %s 
                                      AND stato IN ('in_coda', 'in_elaborazione', 'errore')
                                """, (studio_corrente, azienda_attiva))
                                batch_attivi_db = [r['batch_id'] for r in cursor.fetchall()]
                                
                                # 2. Uniamo i batch attivi con la memoria locale
                                memoria = st.session_state.get("active_batches_queue", [])
                                tutti_i_batch = list(set(batch_attivi_db + memoria))
                                
                                if tutti_i_batch:
                                    format_strings = ','.join(['%s'] * len(tutti_i_batch))
                                    # NOVITÀ: Estraiamo anche il batch_id per raggruppare logicamente i file
                                    cursor.execute(f"""
                                        SELECT batch_id, nome_originale, stato, errore 
                                        FROM processing_queue 
                                        WHERE batch_id IN ({format_strings})
                                        ORDER BY created_at ASC
                                    """, tuple(tutti_i_batch))
                                    
                                    lavori_batch = cursor.fetchall()
                                    
                                    if lavori_batch:
                                        df_lavori = pd.DataFrame(lavori_batch)
                                        
                                        # --- LOGICA TTL (Time-To-Live) DI 5 SECONDI ---
                                        batch_da_mostrare = []
                                        nuova_memoria = []
                                        
                                        for b_id in tutti_i_batch:
                                            df_b = df_lavori[df_lavori['batch_id'] == b_id]
                                            if df_b.empty: continue
                                            
                                            tot_b = len(df_b)
                                            comp_b = len(df_b[df_b['stato'] == 'completato'])
                                            
                                            if comp_b == tot_b and tot_b > 0:
                                                # Il malloppo ha completato l'ultimo file in questo istante
                                                chiave_fine = f"fine_batch_{b_id}"
                                                if chiave_fine not in st.session_state:
                                                    # Salviamo l'orologio interno al millisecondo
                                                    st.session_state[chiave_fine] = time.time()
                                                    batch_da_mostrare.append(b_id)
                                                    nuova_memoria.append(b_id)
                                                else:
                                                    # Controlliamo l'orologio: sono passati meno di 5 secondi?
                                                    if time.time() - st.session_state[chiave_fine] < 3:
                                                        batch_da_mostrare.append(b_id)
                                                        nuova_memoria.append(b_id)
                                                    else:
                                                        # L'orologio scade. Il batch viene ELIMINATO per sempre.
                                                        pass
                                            else:
                                                # Sta ancora lavorando, lo manteniamo in vita
                                                batch_da_mostrare.append(b_id)
                                                nuova_memoria.append(b_id)
                                                
                                        # Puliamo definitivamente la RAM del server dai vecchi caricamenti
                                        st.session_state["active_batches_queue"] = nuova_memoria
                                        
                                        # Creiamo la tabella visiva solo con i batch validi
                                        df_vista = df_lavori[df_lavori['batch_id'].isin(batch_da_mostrare)].copy()
                                        
                                        if not df_vista.empty:
                                            tot_vista = len(df_vista)
                                            in_coda = len(df_vista[df_vista['stato'] == 'in_coda'])
                                            in_elab = len(df_vista[df_vista['stato'] == 'in_elaborazione'])
                                            completati = len(df_vista[df_vista['stato'] == 'completato'])
                                            errori = len(df_vista[df_vista['stato'] == 'errore'])
                                            
                                            st.markdown("<hr style='margin-top: 5px; margin-bottom: 15px;'>", unsafe_allow_html=True)
                                            
                                            if errori == 0:
                                                if completati == tot_vista:
                                                    testo_doc = "1 documento" if tot_vista == 1 else f"{tot_vista} documenti"
                                                    st.success(f"Elaborazione di {testo_doc} terminata!")
                                                else:
                                                    st.caption(f"⚙️ **Elaborazione in background** ({completati} di {tot_vista} completati)")
                                            else:
                                                st.caption(f"⚠️ **Attenzione:** Rilevati {errori} errori durante l'estrazione.")
                                            
                                            progresso = completati / tot_vista if tot_vista > 0 else 0
                                            st.progress(progresso)
                                            
                                            def format_stato_queue(s):
                                                if s == 'in_coda': return "⏳ In attesa"
                                                if s == 'in_elaborazione': return "⚙️ Estrazione..."
                                                if s == 'completato': return "✅ Completato"
                                                if s == 'errore': return "❌ Errore"
                                                return s
                                                
                                            df_tab = df_vista[["nome_originale", "stato", "errore"]].copy()
                                            df_tab['stato'] = df_tab['stato'].apply(format_stato_queue)
                                            df_tab.columns = ["Nome File", "Stato", "Dettaglio"]
                                            
                                            st.dataframe(df_tab, width="stretch", hide_index=True)
                                            
                                            if errori > 0:
                                                if st.button("🧹 Nascondi Avviso", width="stretch", type="secondary"):
                                                    for b_id in batch_da_mostrare:
                                                        cursor.execute("DELETE FROM processing_queue WHERE batch_id = %s AND stato = 'errore'", (b_id,))
                                                    conn.commit()
                                                    st.rerun()
                                                    
                                        elif len(nuova_memoria) == 0 and len(tutti_i_batch) > 0:
                                            # Se il Timer è scaduto e la memoria si è svuotata in questo esatto ciclo,
                                            # ordiniamo un Rerun totale silente per far sparire il box e aggiornare i documenti sotto.
                                            st.rerun()
                                            
                                conn.close()

                        auto_refresh_coda_interna()
                        
                st.markdown("<br>", unsafe_allow_html=True)
                
                if is_cliente:
                    render_upload_box()
                    
                    # Carichiamo i dati solo dopo aver disegnato l'upload
                    with st.spinner("⏳ Sincronizzazione dati in corso..."):
                        df_full, storico_filtrato = load_dati_azienda()
                        
                    if not df_full.empty:
                        st.markdown("### 📄 I tuoi documenti")
                        df_tabella = df_full[["id", "stato", "tipo_documento", "data_formattata", "fornitore", "totale_num"]].copy()
                        
                        def format_stato_cliente(s):
                            if s in ["errore_ai"]: return "❌ Errore"
                            if s in ["caricato", "in_pre_validazione", "Da verificare"]: return "🔵 Inviato"
                            if s in ["inviato_per_validazione", "analizzato"]: return "🔵 Inviato"
                            if s in ["validato", "Confermato"]: return "🔵 Inviato"
                            return "🟡 Da verificare"
                        
                        df_tabella["stato"] = df_tabella["stato"].apply(format_stato_cliente)
                        df_tabella["tipo_documento"] = df_tabella["tipo_documento"].apply(lambda x: str(x).replace("_", " ").title() if pd.notnull(x) else "Fattura")
                        df_tabella.columns = ["ID", "Stato", "Tipo Documento", "Data", "Fornitore/Cliente", "Importo (€)"]
                        event = st.dataframe(df_tabella, column_config={"ID": None, "Importo (€)": st.column_config.NumberColumn("Importo (€)", format="%.2f")}, hide_index=True, width="stretch", selection_mode="single-row", on_select="rerun")
                        
                        if 'event' in locals() and event and len(event.selection.rows) > 0:
                            selected_idx = event.selection.rows[0]
                            selected_id = df_tabella.iloc[selected_idx]["ID"]
                            doc_selezionato = next((d for d in storico_filtrato if d["id"] == selected_id), None)
                            if doc_selezionato:
                                st.session_state["doc_attivo"] = doc_selezionato
                                st.session_state["view"] = "detail"
                                st.rerun()
                else:
                    opzioni_tab = ["📤 CARICA DOCUMENTI", "📊 CRUSCOTTO E LAVORO"]

                    # 1. Memoria globale (ricorda l'ultima scelta in assoluto)
                    if "main_tab" not in st.session_state:
                        st.session_state["main_tab"] = opzioni_tab[0]

                    # 2. Chiave dinamica legata all'azienda
                    chiave_dinamica = f"tab_lavoro_{st.session_state['selected_azienda']}"

                    # 3. INIEZIONE DIRETTA: Inseriamo a forza la nostra memoria globale 
                    # nel "cervello" del nuovo widget PRIMA ancora che venga disegnato.
                    # Rimuoviamo il parametro 'default' perché Streamlit leggerà direttamente da qui!
                    if chiave_dinamica not in st.session_state:
                        st.session_state[chiave_dinamica] = st.session_state["main_tab"]

                    tab_selezionato = st.segmented_control(
                        "Navigazione Lavoro",
                        opzioni_tab,
                        key=chiave_dinamica,
                        label_visibility="collapsed"
                    )

                    # 4. Aggiorniamo la memoria globale se l'utente clicca una voce
                    if tab_selezionato:
                        st.session_state["main_tab"] = tab_selezionato
                    else:
                        # Fallback: se il widget restituisce "vuoto", forziamo l'ultima memoria valida
                        tab_selezionato = st.session_state["main_tab"]

                    # 5. Renderizziamo in base al tab_selezionato reale
                    if tab_selezionato == "📤 CARICA DOCUMENTI":
                        # Forza la distruzione visiva della tabella pesante nel browser
                        with st.spinner("⏳ Caricamento interfaccia..."):
                            time.sleep(0.3)
                        render_upload_box()
                        
                    elif tab_selezionato == "📊 CRUSCOTTO E LAVORO":
                        
                        # 1. CARICAMENTO DATI (Sempre fresco all'apertura o al rerun)
                        with st.spinner("⏳ Sincronizzazione dati in corso..."):
                            df_full, storico_filtrato = load_dati_azienda()
                            
                        # 2. WATCHER INTELLIGENTE
                        @st.fragment(run_every="4s")
                        def silent_auto_refresh(current_row_count):
                            # Non disturbiamo l'utente se sta selezionando righe
                            selezioni_attive = 0
                            if "tabella_doc" in st.session_state and "selection" in st.session_state["tabella_doc"]:
                                selezioni_attive = len(st.session_state["tabella_doc"]["selection"]["rows"])
                                
                            if selezioni_attive == 0:
                                conn = get_db_connection()
                                if conn:
                                    cursor = conn.cursor()
                                    cursor.execute("SELECT COUNT(id) FROM analisi WHERE studio_id = %s AND azienda = %s", (studio_corrente, azienda_attiva))
                                    count_db = cursor.fetchone()[0]
                                    conn.close()
                                    
                                    # Se il DB ha più righe di quelle che stiamo visualizzando ora
                                    if count_db != current_row_count:
                                        # Puliamo la cache dei dati per forzare il ricaricamento al prossimo giro
                                        st.rerun()
                        
                        # Lanciamo il watcher passando il numero di righe correnti
                        silent_auto_refresh(len(df_full))
                        
                        # 3. DISEGNO DELLA TABELLA E METRICHE
                        if not df_full.empty:
                            # Qui seguono le tue metriche e la tua tabella...
                            # ... (continua con il tuo codice)
                            df_todo = df_full[df_full['stato'] != 'validato']
                            
                            c1, c2, c3, c4 = st.columns(4)
                            doc_critici = len(df_todo[df_todo['confidence_score'] < 70])
                            doc_medi = len(df_todo[(df_todo['confidence_score'] >= 70) & (df_todo['confidence_score'] < 90)])
                            doc_alti = len(df_todo[df_todo['confidence_score'] >= 90])
                            doc_da_validare = len(df_todo)
                            
                            c1.metric("🔴 Affidabilità Bassa", doc_critici)
                            c2.metric("🟡 Affidabilità Media", doc_medi)
                            c3.metric("🟢 Affidabilità Alta", doc_alti)
                            c4.metric("🔵 Totale da Validare", doc_da_validare)

                            st.markdown("<br>", unsafe_allow_html=True)
                            
                            with st.container(border=True):
                                col_fil1, col_fil2, col_fil3, col_fil4 = st.columns([2.5, 1.5, 1.5, 1])
                                
                                try: 
                                    min_d = df_full['data_parsed'].min().date()
                                    max_d = df_full['data_parsed'].max().date()
                                except: 
                                    min_d = datetime.date.today()
                                    max_d = datetime.date.today()

                                # --- FIX: Espansione dinamica del filtro date ---
                                # Se arrivano nuovi file in background, allarghiamo il calendario per mostrarli
                                if "f_date" not in st.session_state:
                                    st.session_state["f_date"] = (min_d, max_d)
                                    st.session_state["last_doc_count"] = len(df_full)
                                elif st.session_state.get("last_doc_count", 0) != len(df_full):
                                    st.session_state["f_date"] = (min_d, max_d)
                                    st.session_state["last_doc_count"] = len(df_full)

                                with col_fil1:
                                    date_range = st.date_input("📅 Data Emissione", format="DD/MM/YYYY", key="f_date")
                                with col_fil2:
                                    stato_filtro = st.selectbox("📌 Stato", ["Tutti", "Da Validare", "Validati (Pronti per export)"], key="f_status")
                                with col_fil3:
                                    st.markdown("<br>", unsafe_allow_html=True)
                                    mostra_solo_critici = st.toggle("🚨 Solo Critici", key="f_crit")
                                with col_fil4:
                                    st.markdown("<br>", unsafe_allow_html=True)
                                    def reset_filtri():
                                        st.session_state["f_date"] = (min_d, max_d)
                                        st.session_state["f_status"] = "Tutti"
                                        st.session_state["f_crit"] = False
                                                
                                    st.button("🔄 Reset Filtri", width="stretch", on_click=reset_filtri)

                                df_filtrato = df_full.copy()
                                
                                # Salviamo i documenti senza data valida (es. quelli con affidabilità bassa)
                                mask_senza_data = df_filtrato['data_parsed'].isna()
                                
                                if isinstance(date_range, tuple):
                                    if len(date_range) == 2:
                                        mask_date = (df_filtrato['data_parsed'].dt.date >= date_range[0]) & (df_filtrato['data_parsed'].dt.date <= date_range[1])
                                        df_filtrato = df_filtrato[mask_date | mask_senza_data]
                                    elif len(date_range) == 1:
                                        mask_date = df_filtrato['data_parsed'].dt.date == date_range[0]
                                        df_filtrato = df_filtrato[mask_date | mask_senza_data]
                                elif date_range:
                                    mask_date = df_filtrato['data_parsed'].dt.date == date_range
                                    df_filtrato = df_filtrato[mask_date | mask_senza_data]

                                if stato_filtro == "Da Validare": 
                                    df_filtrato = df_filtrato[df_filtrato['stato'] != 'validato']
                                elif stato_filtro == "Validati (Pronti per export)": 
                                    df_filtrato = df_filtrato[df_filtrato['stato'] == 'validato']

                                if mostra_solo_critici:
                                    df_filtrato = df_filtrato[(df_filtrato['confidence_score'] < 70) | (df_filtrato['stato'] == 'errore_ai')]

                                st.markdown("<br>", unsafe_allow_html=True)
                                if not df_filtrato.empty:
                                    # --- AGGIUNTO 'numero_fattura' NELLA LISTA ---
                                    df_tabella = df_filtrato[["id", "stato", "tipo_documento", "direzione", "data_formattata", "numero_fattura", "fornitore", "totale_num", "confidence_score", "flag_estero"]].copy()
                                    
                                    # --- AUTOCLASSIFICAZIONE VISIVA TABELLA ---
                                    def correggi_tipo_estero(row):
                                        tipo = str(row.get('tipo_documento', 'FATTURA')).upper()
                                        flag = str_to_bool(row.get('flag_estero', False))
                                        if flag and tipo not in ["AUTOFATTURA", "NOTA_CREDITO", "PRESTAZIONE_OCCASIONALE", "DOCUMENTO_COMMERCIALE"]:
                                            return "FATTURA_ESTERA"
                                        return tipo
                                        
                                    df_tabella["tipo_documento"] = df_tabella.apply(correggi_tipo_estero, axis=1)
                                    df_tabella = df_tabella.drop(columns=["flag_estero"]) # Rimuoviamo la colonna di servizio
                                    
                                    # --- FORMATTAZIONE NORMALE ---
                                    df_tabella["direzione"] = df_tabella["direzione"].apply(lambda x: "IN" if x == "ENTRATA" else "OUT")
                                    df_tabella["tipo_documento"] = df_tabella["tipo_documento"].apply(lambda x: str(x).replace("_", " ").title() if pd.notnull(x) else "Fattura")
                                    
                                    # --- NUOVA LOGICA: STATO E SCORE UNIFICATI ---
                                    def format_stato_score(row):
                                        s = row["stato"]
                                        c = row["confidence_score"]
                                        
                                        # Se validato a mano, vince la validazione
                                        if s in ["validato", "Confermato"]: return "✅ Validato"
                                        if s in ["errore_ai"]: return "❌ Errore AI"
                                        if s in ["caricato", "in_pre_validazione", "Da verificare"]: return "🟡 Cliente"
                                        
                                        # Altrimenti mostriamo la percentuale AI
                                        try: 
                                            v = int(float(c))
                                        except: 
                                            v = 0
                                            
                                        if v >= 95: return f"🟢 {v}%"
                                        elif v >= 90: return f"🟢 {v}%"
                                        elif v >= 70: return f"🟡 {v}%"
                                        else: return f"🔴 {v}%"
                                        
                                    df_tabella["stato_unificato"] = df_tabella.apply(format_stato_score, axis=1)
                                    
                                    # Selezioniamo e riordiniamo le colonne secondo il nuovo ordine (mantenendo l'ID nascosto all'inizio per il funzionamento dei bottoni)
                                    df_tabella = df_tabella[["id", "data_formattata", "numero_fattura", "tipo_documento", "fornitore", "totale_num", "direzione", "stato_unificato"]]
                                    
                                    # Rinominiamo per l'interfaccia utente
                                    df_tabella.columns = ["ID", "Data", "N° Doc.", "Tipo", "Controparte", "Importo (€)", "Flusso", "Stato"]
                                    
                                    # --- 1. INTERCETTIAMO LA SELEZIONE DEI QUADRATINI ---
                                    righe_selezionate_export = []
                                    if "tabella_doc" in st.session_state and "selection" in st.session_state["tabella_doc"]:
                                        righe_selezionate_export = st.session_state["tabella_doc"]["selection"]["rows"]
                                        
                                    # Creiamo il dataframe per l'export: di base esporta tutto quello che è filtrato
                                    df_export = df_filtrato.copy()
                                    
                                    # Se l'utente ha spuntato almeno un quadratino, filtriamo solo quei documenti!
                                    if len(righe_selezionate_export) > 0:
                                        ids_selezionati = df_tabella.iloc[righe_selezionate_export]["ID"].tolist()
                                        df_export = df_export[df_export["id"].isin(ids_selezionati)]
                                    
                                    c_info, c_fmt, c_btn = st.columns([5, 2, 2], vertical_alignment="center")
                                    
                                    # Aggiorniamo il testo dinamicamente
                                    testo_info = f"📄 **{len(df_tabella)} documenti trovati**."
                                    if len(righe_selezionate_export) > 0:
                                        testo_info += f" Hai spuntato **{len(righe_selezionate_export)}** doc da esportare 👉"
                                    else:
                                        testo_info += " Clicca su una riga per aprirla, o esporta in blocco 👉"
                                        
                                    c_info.caption(testo_info)
                                    
                                    tipo_export = c_fmt.selectbox("Formato", ["Standard (Excel)", "CSV Zucchetti", "CSV TeamSystem"], label_visibility="collapsed")
                                    
                                    buffer = io.BytesIO()
                                    file_n = ""
                                    mime_t = ""
                                    
                                    if tipo_export == "Standard (Excel)":
                                        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                                            df_export['imponibile_calc'] = df_export['totale_num'] - df_export['iva_euro_num']
                                            
                                            cols_to_export = [
                                                "data_formattata", 
                                                "numero_fattura", 
                                                "fornitore", 
                                                "piva", 
                                                "tipo_documento", 
                                                "direzione", 
                                                "imponibile_calc", 
                                                "iva_euro_num", 
                                                "totale_num", 
                                                "ritenuta_acconto",
                                                "flag_estero"
                                            ]
                                            
                                            extra_cols = []
                                            if "categoria_contabile" in df_export.columns: extra_cols.append("categoria_contabile")
                                            if "codice_conto" in df_export.columns: extra_cols.append("codice_conto")
                                            extra_cols.append("stato")
                                            
                                            final_cols = cols_to_export + extra_cols
                                            
                                            for c in final_cols:
                                                if c not in df_export.columns:
                                                    df_export[c] = None
                                                    
                                            df_excel = df_export[final_cols]
                                            
                                            rename_dict = {
                                                "data_formattata": "Data",
                                                "numero_fattura": "Numero",
                                                "fornitore": "Fornitore/Cliente",
                                                "piva": "Partita IVA",
                                                "tipo_documento": "Tipo Documento",
                                                "direzione": "Direzione",
                                                "imponibile_calc": "Imponibile",
                                                "iva_euro_num": "IVA",
                                                "totale_num": "Totale",
                                                "ritenuta_acconto": "Ritenuta",
                                                "flag_estero": "Estero",
                                                "categoria_contabile": "Categoria Contabile",
                                                "codice_conto": "Codice Conto",
                                                "stato": "Stato DB"
                                            }
                                            df_excel = df_excel.rename(columns=rename_dict)
                                            df_excel.to_excel(writer, index=False, sheet_name='Export_Gestionale')
                                            
                                        mime_t = "application/vnd.ms-excel"
                                        file_n = f"Export_{azienda_attiva}.xlsx"
                                        
                                    elif "Zucchetti" in tipo_export:
                                        df_z = pd.DataFrame()
                                        mappe_export = get_mappature(studio_corrente)
                                        
                                        def calcola_causale_zucchetti(row):
                                            tipo = row.get("tipo_documento", "FATTURA")
                                            direz = row.get("direzione", "USCITA")
                                            if tipo == "PRESTAZIONE_OCCASIONALE":
                                                return "PO-V" if direz == "ENTRATA" else "PO-A"
                                            elif tipo == "AUTOFATTURA":
                                                return "AF-V" if direz == "ENTRATA" else "AF-A"
                                            elif tipo == "NOTA_CREDITO":
                                                return "NC-V" if direz == "ENTRATA" else "NC-A"
                                            else:
                                                return "FV" if direz == "ENTRATA" else "FA"
                                                
                                        def risolvi_conto_gestionale(row):
                                            if 'codice_conto' in row and pd.notnull(row['codice_conto']) and str(row['codice_conto']).strip() != "":
                                                return str(row['codice_conto']).strip()
                                            cat = str(row.get("categoria_contabile", ""))
                                            return str(mappe_export.get(cat, {}).get("codice_conto", "")).strip()
                                                
                                        df_z["Causale"] = df_export.apply(calcola_causale_zucchetti, axis=1)
                                        df_z["Sezionale"] = "1"
                                        df_z["DataReg"] = df_export["data_formattata"]
                                        df_z["NumDoc"] = df_export["numero_fattura"]
                                        df_z["RagioneSociale"] = df_export["fornitore"]
                                        df_z["PIVA_Controparte"] = df_export["piva"]
                                        df_z["CF_Controparte"] = df_export.get("codice_fiscale", "")
                                        df_z["Descrizione"] = df_export.get("descrizione", "")
                                        df_z["TotaleDocumento"] = df_export["totale_num"]
                                        df_z["Imponibile"] = df_export["totale_num"] - df_export["iva_euro_num"]
                                        df_z["ImpostaIVA"] = df_export["iva_euro_num"]
                                        df_z["Codice IVA gestionale"] = df_export["iva_perc"].apply(lambda x: str(x).replace('%', '').strip() if pd.notnull(x) else "0")
                                        df_z["Codice conto gestionale"] = df_export.apply(risolvi_conto_gestionale, axis=1)
                                        df_z["RitenutaAcconto"] = df_export.get("ritenuta_acconto", 0.0)
                                        
                                        buffer.write(df_z.to_csv(index=False, sep=";", decimal=",").encode('windows-1252', errors='replace'))
                                        mime_t = "text/csv"
                                        file_n = f"Zucchetti_{azienda_attiva}.csv"
                                    else:
                                        df_t = pd.DataFrame()
                                        df_t["DataMovimento"] = df_export["data_formattata"]
                                        
                                        def calcola_tipo_teamsystem(row):
                                            tipo = row.get("tipo_documento", "FATTURA")
                                            direz = row.get("direzione", "USCITA")
                                            suffisso = "IN" if direz == "ENTRATA" else "OUT"
                                            if tipo == "PRESTAZIONE_OCCASIONALE":
                                                return f"PREST_OCCASIONALE_{suffisso}"
                                            elif tipo == "AUTOFATTURA":
                                                return f"AUTOFATTURA_{suffisso}"
                                            elif tipo == "NOTA_CREDITO":
                                                return f"NOTA_CREDITO_{suffisso}"
                                            else:
                                                return direz
                                                
                                        df_t["Tipo"] = df_export.apply(calcola_tipo_teamsystem, axis=1)
                                        df_t["PartitaIVA"] = df_export["piva"]
                                        df_t["ImportoTotale"] = df_export["totale_num"]
                                        df_t["ImpostaIVA"] = df_export["iva_euro_num"]
                                        df_t["RitenutaAcconto"] = df_export["ritenuta_acconto"] if "ritenuta_acconto" in df_export.columns else 0.0
                                        
                                        buffer.write(df_t.to_csv(index=False, sep=";").encode('utf-8'))
                                        mime_t = "text/csv"
                                        file_n = f"TeamSystem_{azienda_attiva}.csv"
                                    
                                    if c_btn.download_button(label=f"⬇️ Scarica file", data=buffer.getvalue(), file_name=file_n, mime=mime_t, type="primary", width="stretch"):
                                        log_action(studio_corrente, st.session_state['user'], f"Esportato {tipo_export}")
                                    
                                    # --- FUNZIONE PER COLORARE IL TESTO DELLA COLONNA FLUSSO ---
                                    def colora_flusso(val):
                                        if val == "IN":
                                            return "color: #28a745; font-weight: bold;" # Verde brillante
                                        elif val == "OUT":
                                            return "color: #dc3545; font-weight: bold;" # Rosso acceso
                                        return ""

                                    # Applichiamo lo stile al dataframe (la colonna è stata rinominata in 'Flusso')
                                    df_styled = df_tabella.style.map(colora_flusso, subset=["Flusso"])
                                    
                                    # --- 2. INIZIALIZZIAMO LA TABELLA CON UNA KEY ---
                                    event = st.dataframe(
                                        df_styled,  # <--- PASSAMO IL DATAFRAME STILIZZATO (df_styled invece di df_tabella)
                                        column_config={
                                            "ID": None, 
                                            "Importo (€)": st.column_config.NumberColumn("Importo (€)", format="%.2f")
                                        },
                                        hide_index=True, width="stretch", 
                                        selection_mode="multi-row",
                                        on_select="rerun",
                                        key="tabella_doc"  # <--- FONDAMENTALE PER FAR FUNZIONARE LA SELEZIONE!
                                    )
                                    
                                    # --- 3. BOTTONI REVISIONA E ELIMINA (RIMANGONO AL LORO POSTO!) ---
                                    if 'event' in locals() and event and len(event.selection.rows) > 0:
                                        righe_selezionate = event.selection.rows
                                        
                                        st.markdown("<br>", unsafe_allow_html=True)
                                        col_azione1, col_azione2, _ = st.columns([1, 1.5, 3])
                                        
                                        # Mostra il tasto Apri SOLO se è spuntata esattamente UNA riga
                                        if len(righe_selezionate) == 1:
                                            if col_azione1.button("👁️ Revisiona Documento", type="primary"):
                                                selected_idx = righe_selezionate[0]
                                                selected_id = df_tabella.iloc[selected_idx]["ID"]
                                                doc_selezionato = next((d for d in storico_filtrato if d["id"] == selected_id), None)
                                                if doc_selezionato:
                                                    st.session_state["doc_attivo"] = doc_selezionato
                                                    st.session_state["view"] = "detail"
                                                    st.rerun()
                                                    
                                        # Mostra il tasto Elimina per 1 o PIÙ righe
                                        if col_azione2.button(f"🗑️ Elimina Selezionati ({len(righe_selezionate)})", type="secondary"):
                                            ids_da_eliminare = [int(df_tabella.iloc[idx]["ID"]) for idx in righe_selezionate]
                                            
                                            conn = get_db_connection()
                                            if conn:
                                                cursor = conn.cursor()
                                                format_strings = ','.join(['%s'] * len(ids_da_eliminare))
                                                
                                                cursor.execute(f"SELECT file_path FROM analisi WHERE id IN ({format_strings}) AND studio_id = %s", tuple(ids_da_eliminare + [studio_corrente]))
                                                files_to_delete = cursor.fetchall()
                                                for file_row in files_to_delete:
                                                    if file_row[0] and os.path.exists(file_row[0]):
                                                        try: os.remove(file_row[0])
                                                        except: pass

                                                cursor.execute(f"DELETE FROM analisi WHERE id IN ({format_strings}) AND studio_id = %s", tuple(ids_da_eliminare + [studio_corrente]))
                                                conn.commit()
                                                
                                                log_action(studio_corrente, st.session_state['user'], f"Eliminazione di massa: {len(ids_da_eliminare)} documenti")
                                                conn.close()
                                                
                                                st.success(f"✅ {len(ids_da_eliminare)} documenti eliminati con successo!")
                                                time.sleep(1)
                                                st.rerun()
                                    # --- FINE TABELLA MODIFICATA ---
                                else:
                                    st.info("Nessun documento corrisponde ai criteri di filtro selezionati.")
                        else:
                            st.info("Non ci sono documenti per questa azienda. Vai nella scheda '📤 CARICA DOCUMENTI' per iniziare.")
    
        # ==========================================
        # VISTA OPERATORE: ESTRAZIONE E GENERAZIONE XML (CON DB MYSQL)
        # ==========================================
        elif st.session_state["view"] == "generatore_xml" and is_operatore:
            st.title("📝 Generazione XML da Cartaceo")
            
            # --- CARICAMENTO RUBRICA DAL DB ---
            rubrica = get_rubrica_xml(studio_corrente)
            
            with st.expander("📖 Gestione Rubrica (Aggiungi o Elimina)", expanded=False):
                tab_aggiungi, tab_elimina = st.tabs(["➕ Nuovo Contatto", "🗑️ Elimina Contatto"])
                
                with tab_aggiungi:
                    with st.form("form_aggiungi_rubrica_db"):
                        st.markdown("Crea un nuovo profilo completo da usare nei menu a tendina.")
                        
                        st.markdown("**Dati Anagrafici**")
                        c_tipo1, c_tipo2 = st.columns(2)
                        f_den = c_tipo1.text_input("Ragione Sociale (se Azienda)")
                        f_nome_completo = c_tipo2.text_input("Nome e Cognome (se Persona Fisica)")
                        
                        c_fisc1, c_fisc2 = st.columns(2)
                        f_piva = c_fisc1.text_input("Partita IVA")
                        f_cf = c_fisc2.text_input("Codice Fiscale")
                        
                        st.markdown("**Sede Legale**")
                        c_ind1, c_ind2, c_ind3, c_ind4 = st.columns([2, 1, 1, 2])
                        f_ind = c_ind1.text_input("Indirizzo (es. Via Roma)")
                        f_civ = c_ind2.text_input("Civico")
                        f_cap = c_ind3.text_input("CAP")
                        f_com = c_ind4.text_input("Comune")
                        f_prov = c_ind1.text_input("Provincia (Sigla, es. MI)")
                        
                        st.markdown("**Contatti e Iscrizione REA (Opzionali)**")
                        c_cont1, c_cont2 = st.columns(2)
                        f_pec = c_cont1.text_input("Indirizzo PEC")
                        f_email = c_cont2.text_input("Indirizzo Email")
                        
                        c_rea1, c_rea2, c_rea3 = st.columns(3)
                        f_rea_uff = c_rea1.text_input("Ufficio REA (Sigla, es. EN)")
                        f_rea_num = c_rea2.text_input("Numero REA (es. 57440)")
                        f_rea_liq = c_rea3.selectbox("Stato Liquidazione (Opzionale)", ["", "LN", "LS"])
                        
                        if st.form_submit_button("💾 Salva nel Database", type="primary"):
                            if not f_den and not f_nome_completo:
                                st.error("Inserisci almeno la Ragione Sociale o il Nome/Cognome.")
                            else:
                                nome_spl = f_nome_completo.split(" ")[0] if f_nome_completo else ""
                                cognome_spl = " ".join(f_nome_completo.split(" ")[1:]) if f_nome_completo else ""
                                add_rubrica_xml(studio_corrente, f_den, nome_spl, cognome_spl, f_piva, f_cf, f_ind, f_civ, f_cap, f_com, f_prov, f_pec, f_email, f_rea_uff, f_rea_num, f_rea_liq)
                                st.success("Contatto salvato con successo!")
                                time.sleep(1)
                                st.rerun()

                with tab_elimina:
                    if rubrica:
                        st.markdown("Seleziona il contatto che desideri rimuovere definitivamente dal database.")
                        contatto_da_eliminare = st.selectbox("Contatto da eliminare", list(rubrica.keys()), key="del_rub_sel")
                        
                        if st.button("❌ Conferma Eliminazione", type="primary"):
                            id_da_rimuovere = rubrica[contatto_da_eliminare]['id']
                            delete_rubrica_xml(id_da_rimuovere, studio_corrente)
                            st.success(f"Contatto '{contatto_da_eliminare}' eliminato con successo!")
                            time.sleep(1)
                            st.rerun()
                    else:
                        st.info("La rubrica è attualmente vuota.")

            st.divider()

            col_pdf, col_form = st.columns([1, 1.8])
            
            nomi_rubrica = ["--- Seleziona ---"] + list(rubrica.keys())
            
            with col_pdf:
                with st.container(border=True):
                    st.subheader("📄 Documento Sorgente")
                    
                    mittente_scelto = st.selectbox("👤 Mittente (Cedente)", nomi_rubrica, key="sel_mitt")
                    destinatario_scelto = st.selectbox("🎯 Destinatario (Cessionario)", nomi_rubrica, key="sel_dest")
                    st.markdown("<br>", unsafe_allow_html=True)
                    
                    file_xml = st.file_uploader("Carica fattura o scontrino", type=["jpg", "png", "jpeg", "pdf"], label_visibility="collapsed")
                    
                    if file_xml:
                        if file_xml.name.endswith(('jpg','png','jpeg')):
                            st.image(file_xml, width="stretch")
                        else:
                            st.success("PDF caricato e pronto per l'invio.")
                            
                        # Mettiamo il job in coda e non blocchiamo l'interfaccia
                        if "xml_batch_id" not in st.session_state:
                            if st.button("🚀 Metti in Coda di Estrazione", type="primary", width="stretch"):
                                if mittente_scelto == "--- Seleziona ---" or destinatario_scelto == "--- Seleziona ---":
                                    st.error("⚠️ Seleziona Mittente e Destinatario dai menu a tendina prima di estrarre!")
                                else:
                                    batch_id = str(uuid.uuid4())
                                    conn = get_db_connection()
                                    if conn:
                                        cursor = conn.cursor()
                                        # Cartella specifica usando il nome segreto
                                        save_dir = os.path.join(UPLOAD_DIR, studio_corrente, "XML_OPERATOR_JOB", "QUEUE")
                                        os.makedirs(save_dir, exist_ok=True)
                                        
                                        file_path = os.path.join(save_dir, f"{uuid.uuid4().hex}_{sanitize_filename(file_xml.name)}")
                                        
                                        with open(file_path, "wb") as out_f: out_f.write(file_xml.getvalue())
                                            
                                        # Iniettiamo l'azienda finta per attivare il bypass nel worker
                                        cursor.execute("""
                                            INSERT INTO processing_queue (batch_id, studio_id, utente_id, azienda, file_path, nome_originale, stato)
                                            VALUES (%s, %s, %s, 'XML_OPERATOR_JOB', %s, %s, 'in_coda')
                                        """, (batch_id, studio_corrente, st.session_state['user'], file_path, file_xml.name))
                                        
                                        conn.commit()
                                        conn.close()
                                        
                                        st.session_state["xml_batch_id"] = batch_id
                                        st.session_state["xml_file_path"] = file_path
                                        st.rerun()

                    # --- FRAGMENT AUTO-AGGIORNANTE OPERATORE ---
                    if "xml_batch_id" in st.session_state:
                        st.markdown("<hr style='margin-top: 5px; margin-bottom: 15px;'>", unsafe_allow_html=True)
                        @st.fragment(run_every="3s")
                        def auto_refresh_xml_queue():
                            conn = get_db_connection()
                            if conn:
                                cursor = conn.cursor(dictionary=True)
                                cursor.execute("SELECT stato, errore FROM processing_queue WHERE batch_id = %s", (st.session_state["xml_batch_id"],))
                                job = cursor.fetchone()
                                conn.close()
                                
                                if job:
                                    if job['stato'] in ['in_coda', 'in_elaborazione']:
                                        st.info("⏳ Motore IA in estrazione. Nessun blocco, puoi continuare a lavorare...")
                                        st.progress(0.5 if job['stato'] == 'in_elaborazione' else 0.1)
                                    elif job['stato'] == 'errore':
                                        st.error(f"❌ Errore IA: {job['errore']}")
                                        if st.button("🔄 Riprova o Annulla", width="stretch"):
                                            del st.session_state["xml_batch_id"]
                                            st.rerun()
                                    elif job['stato'] == 'completato':
                                        # Il worker ha finito. Leggiamo il risultato dal disco.
                                        json_path = st.session_state["xml_file_path"] + ".json"
                                        if os.path.exists(json_path):
                                            with open(json_path, "r") as jf:
                                                draft_ia = json.load(jf)
                                                
                                            # Ricostruzione Dati Rubrica
                                            rm = rubrica[st.session_state["sel_mitt"]]
                                            rd = rubrica[st.session_state["sel_dest"]]
                                            
                                            draft_ia["pec_destinatario"] = rd.get("pec") or "0000000"
                                            
                                            draft_ia["cedente"] = {
                                                "denominazione": rm.get("denominazione") or "",
                                                "nome": rm.get("nome") or "",
                                                "cognome": rm.get("cognome") or "",
                                                "piva": rm.get("partita_iva") or "",
                                                "cf": rm.get("codice_fiscale") or rm.get("partita_iva") or "",
                                                "indirizzo": rm.get("indirizzo") or "",
                                                "civico": rm.get("civico") or "",
                                                "cap": rm.get("cap") or "",
                                                "comune": rm.get("comune") or "",
                                                "provincia": rm.get("provincia") or "",
                                                "regime": rm.get("regime_fiscale") or "RF01",
                                                "email": rm.get("email") or "",
                                                "rea_ufficio": rm.get("rea_ufficio") or "",
                                                "rea_numero": rm.get("rea_numero") or "",
                                                "rea_liquidazione": rm.get("rea_liquidazione") or "LN"
                                            }
                                            
                                            draft_ia["cessionario"] = {
                                                "denominazione": rd.get("denominazione") or "",
                                                "nome": rd.get("nome") or "",
                                                "cognome": rd.get("cognome") or "",
                                                "piva": rd.get("partita_iva") or "",
                                                "cf": rd.get("codice_fiscale") or "",
                                                "indirizzo": rd.get("indirizzo") or "",
                                                "civico": rd.get("civico") or "",
                                                "cap": rd.get("cap") or "",
                                                "comune": rd.get("comune") or "",
                                                "provincia": rd.get("provincia") or ""
                                            }
                                            
                                            st.session_state["xml_draft"] = draft_ia
                                            del st.session_state["xml_batch_id"]
                                            st.rerun()
                                        else:
                                            st.error("Dati non trovati. Controlla il worker sul terminale.")
                        auto_refresh_xml_queue()

            with col_form:
                with st.container(border=True):
                    st.subheader("✍️ Revisione e Creazione")
                    if "xml_draft" in st.session_state:
                        dati = st.session_state["xml_draft"]
                        
                        nome_ced = dati['cedente'].get('denominazione') or f"{dati['cedente'].get('nome')} {dati['cedente'].get('cognome')}"
                        nome_ces = dati['cessionario'].get('denominazione') or f"{dati['cessionario'].get('nome')} {dati['cessionario'].get('cognome')}"
                        st.success(f"**Mittente:** {nome_ced} | **Destinatario:** {nome_ces}")
                        
                        c1, c2, c3, c4 = st.columns([1.5, 1, 1.5, 1.5])
                        # Il progressivo è fondamentale per Asit/SdI (max 5 caratteri alfanumerici)
                        dati["progressivo"] = c1.text_input("Progr. Invio (es. 00001)", dati.get("progressivo", "00001"))
                        dati["dati_generali"]["numero"] = c2.text_input("Numero", dati["dati_generali"]["numero"])
                        dati["dati_generali"]["data"] = c3.text_input("Data (YYYY-MM-DD)", dati["dati_generali"]["data"])
                        dati["dati_generali"]["importo_totale"] = c4.number_input("Totale (€)", value=float(dati["dati_generali"]["importo_totale"]))
                        
                        st.markdown("**Dettaglio Prodotti Estratti**")
                        df_linee = pd.DataFrame(dati["linee"])
                        edited_linee = st.data_editor(df_linee, num_rows="dynamic", width="stretch", hide_index=True)
                        
                        st.divider()
                        
                        if st.button("⚡ Genera XML", type="primary", width="stretch"):
                            dati["linee"] = edited_linee.to_dict('records')
                            
                            # Identificativo fiscale del cedente
                            piva_file = dati['cedente']['piva'] if dati['cedente']['piva'] else dati['cedente']['cf']
                            piva_file = piva_file.strip().upper()
                            
                            # Generazione nome file standard SDI richiesto da Asit
                            prog = str(dati.get("progressivo", "00001")).strip()
                            nome_file_sdi = f"IT{piva_file}_{prog}.xml"
                            
                            try:
                                xml_file = genera_xml_fattura_avanzata(dati)
                                st.download_button(f"⬇️ Scarica {nome_file_sdi}", data=xml_file, file_name=nome_file_sdi, mime="application/xml", type="secondary", width="stretch")
                            except Exception as err:
                                st.error(f"Errore generazione XML: {err}")
                    else:
                        st.info("Seleziona mittente e destinatario, poi carica un documento ed estrai i dettagli per sbloccare la console.")
    
    except Exception as e:
        logging.error(f"CRITICAL SYSTEM FAILURE: {e}", exc_info=True)
        st.error("🚨 **Si è verificato un errore critico di sistema.**")
        st.markdown("Il problema è stato registrato e notificato al nostro team tecnico. I tuoi dati sono al sicuro.")
        if st.button("Riavvia Sessione di Lavoro", type="primary"):
            st.rerun()