import time
import logging
import traceback
import os
import shutil
import mysql.connector
from document_processor import processa_singolo_documento

def carica_segreti(filepath=".streamlit/secrets.toml"):
    """Legge manualmente il file TOML senza dipendere da Streamlit o librerie esterne."""
    segreti = {}
    base_dir = os.path.dirname(os.path.abspath(__file__))
    percorso_completo = os.path.join(base_dir, filepath)
    
    try:
        with open(percorso_completo, "r", encoding="utf-8") as f:
            for riga in f:
                riga = riga.split('#')[0].strip() # Rimuove i commenti
                if '=' in riga:
                    chiave, valore = riga.split('=', 1)
                    # Pulisce spazi e virgolette
                    segreti[chiave.strip()] = valore.strip().strip('"').strip("'")
        return segreti
    except FileNotFoundError:
        logging.error(f"ATTENZIONE CRITICA: File {percorso_completo} non trovato. Il worker fallirà.")
        return {}

# --- CARICAMENTO CREDENZIALI ---
SEGRETI = carica_segreti()

DB_HOST = SEGRETI.get("DB_HOST", "localhost")
DB_USER = SEGRETI.get("DB_USER", "root")
DB_PASSWORD = SEGRETI.get("DB_PASSWORD", "")
DB_NAME = SEGRETI.get("DB_NAME", "")
GEMINI_API_KEY = SEGRETI.get("GEMINI_API_KEY", "")

CATEGORIE_IA = ["Utenze", "Carburante", "Cancelleria", "Consulenze", "Merce", "Ristorante", "Spese Mediche", "Attrezzature", "Servizi Web", "Altro"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - WORKER - %(levelname)s - %(message)s')

def get_db_connection():
    return mysql.connector.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, database=DB_NAME)

def recupera_zombie(conn):
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE processing_queue 
        SET stato = 'in_coda', 
            tentativi = tentativi + 1, 
            errore = 'Timeout 60m. Riprovo.',
            finished_at = NULL
        WHERE stato = 'in_elaborazione' 
          AND started_at < NOW() - INTERVAL 1 HOUR
          AND tentativi < 3
    """)
    # COMMIT OBBLIGATORIO SEMPRE, chiude la transazione implicita
    conn.commit() 
    
    if cursor.rowcount > 0:
        logging.warning(f"Recuperati {cursor.rowcount} job zombie.")
        
    cursor.close()

def rimuovi_file_queue(file_path):
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"File rimosso correttamente dalla coda: {file_path}")
    except Exception as e:
        logging.error(f"Impossibile rimuovere il file {file_path}: {e}")

def sposta_file_in_errore(file_path):
    """Sposta il file in ERRORI_QUEUE per analisi manuale in caso di fallimento definitivo."""
    try:
        if file_path and os.path.exists(file_path):
            dir_queue = os.path.dirname(file_path) 
            dir_azienda = os.path.dirname(dir_queue) 
            dir_errori = os.path.join(dir_azienda, "ERRORI_QUEUE")
            
            os.makedirs(dir_errori, exist_ok=True)
            nome_file = os.path.basename(file_path)
            shutil.move(file_path, os.path.join(dir_errori, nome_file))
            logging.info(f"File fallito archiviato in: {dir_errori}")
    except Exception as e:
        logging.error(f"Impossibile spostare il file in errore {file_path}: {e}")

def run_worker():
    logging.info("Worker IA avviato. In attesa di documenti...")
    
    while True:
        conn = None
        try:
            conn = get_db_connection()
            recupera_zombie(conn)
            
            # CONTRATTO SODDISFATTO: Cursore inizializzato come Dictionary
            cursor = conn.cursor(dictionary=True)
            
            conn.start_transaction()
            cursor.execute("""
                SELECT * FROM processing_queue 
                WHERE stato = 'in_coda' AND tentativi < 3
                ORDER BY created_at ASC LIMIT 1 
                FOR UPDATE
            """)
            job = cursor.fetchone()
            
            if not job:
                conn.commit()
                conn.close()
                time.sleep(3)
                continue
                
            job_id = job['id']
            # finished_at = NULL in caso provenga da un retry precedente
            cursor.execute("UPDATE processing_queue SET stato = 'in_elaborazione', started_at = NOW(), finished_at = NULL WHERE id = %s", (job_id,))
            conn.commit()
            
            logging.info(f"Elaborazione Batch {job['batch_id']} | Job {job_id} | File: {job['nome_originale']}")
            
            conn.start_transaction()
            try:
                processa_singolo_documento(
                    cursor=cursor,
                    file_path=job['file_path'],
                    nome_originale=job['nome_originale'],
                    studio_id=job['studio_id'],
                    utente_id=job['utente_id'],
                    azienda=job['azienda'],
                    api_key=GEMINI_API_KEY,
                    categorie_ia=CATEGORIE_IA
                )
                
                cursor.execute("UPDATE processing_queue SET stato = 'completato', finished_at = NOW(), errore = NULL WHERE id = %s", (job_id,))
                
                conn.commit()
                logging.info(f"Job {job_id} completato con successo.")
                
                rimuovi_file_queue(job['file_path'])
                
            except Exception as run_err:
                conn.rollback()
                err_msg = str(run_err) + "\n" + traceback.format_exc()
                logging.error(f"Errore logica IA su Job {job_id}: {err_msg}")
                
                conn.start_transaction()
                nuovo_tentativo = job['tentativi'] + 1
                nuovo_stato = 'errore' if nuovo_tentativo >= 3 else 'in_coda'
                
                # finished_at popolato SOLO se lo stato è terminale (errore)
                cursor.execute("""
                    UPDATE processing_queue 
                    SET stato = %s, errore = %s, tentativi = %s,
                        finished_at = IF(%s = 'errore', NOW(), NULL)
                    WHERE id = %s
                """, (nuovo_stato, str(run_err), nuovo_tentativo, nuovo_stato, job_id))
                conn.commit()
                
                if nuovo_stato == 'errore':
                    logging.warning(f"Job {job_id} fallito definitivamente. Sposto il file in ERRORI_QUEUE.")
                    sposta_file_in_errore(job['file_path'])
            
        except mysql.connector.Error as db_err:
            logging.error(f"Errore di connettività DB: {db_err}")
            time.sleep(10)
        except Exception as e:
            logging.error(f"Eccezione non gestita nel core loop: {e}")
            time.sleep(5)
        finally:
            if conn and conn.is_connected():
                conn.close()

if __name__ == "__main__":
    run_worker()