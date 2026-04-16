-- ==========================================================
-- SETUP DATABASE PER AI-DOC AUTOMATION STUDIO (SaaS B2B)
-- ==========================================================

-- Crea il database se non esiste (Sostituisci il nome se usi uno diverso)
CREATE DATABASE IF NOT EXISTS ai_doc_studio CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE ai_doc_studio;

-- 1. TABELLA: Studi Commercialisti (I tuoi clienti principali del SaaS)
CREATE TABLE IF NOT EXISTS studi_commercialisti (
    id_studio VARCHAR(50) PRIMARY KEY,
    ragione_sociale VARCHAR(150) NOT NULL,
    partita_iva VARCHAR(20),
    data_creazione TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. TABELLA: Utenti (Contiene Admin, Commercialisti e Clienti Aziende)
CREATE TABLE IF NOT EXISTS utenti (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password VARCHAR(255) NOT NULL, -- Lunghezza 255 per supportare l'hash bcrypt
    studio_id VARCHAR(50) NOT NULL,
    ruolo VARCHAR(50) DEFAULT 'commercialista', -- 'admin', 'commercialista', 'cliente'
    nome_azienda VARCHAR(150) NULL,
    data_creazione TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. TABELLA: Aziende (Le aziende gestite dagli Studi Commercialisti)
CREATE TABLE IF NOT EXISTS aziende (
    id INT AUTO_INCREMENT PRIMARY KEY,
    studio_id VARCHAR(50) NOT NULL,
    nome VARCHAR(150) NOT NULL,
    partita_iva VARCHAR(20) NOT NULL,
    codice_fiscale VARCHAR(16),
    indirizzo VARCHAR(255),
    cap VARCHAR(10),
    citta VARCHAR(100),
    provincia VARCHAR(10),
    data_registrazione TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. TABELLA: Mappature Conti (Le regole Zucchetti/TeamSystem di ogni Studio)
CREATE TABLE IF NOT EXISTS mappature_conti (
    id INT AUTO_INCREMENT PRIMARY KEY,
    studio_id VARCHAR(50) NOT NULL,
    categoria_ia VARCHAR(100) NOT NULL,
    codice_conto VARCHAR(50),
    codice_iva VARCHAR(50)
);

-- 5. TABELLA: Analisi (Il cuore del sistema: fatture, scontrini, dati IA)
CREATE TABLE IF NOT EXISTS analisi (
    id INT AUTO_INCREMENT PRIMARY KEY,
    studio_id VARCHAR(50) NOT NULL,
    azienda VARCHAR(150) NOT NULL,
    direzione VARCHAR(10) NOT NULL, -- 'ENTRATA' o 'USCITA'
    
    -- Dati Documento
    fornitore VARCHAR(150),
    numero_fattura VARCHAR(50),
    piva VARCHAR(20),
    codice_fiscale VARCHAR(16),
    data_doc VARCHAR(50),
    
    -- Dati Contabili
    totale DECIMAL(12,2) DEFAULT 0.00,
    iva_perc VARCHAR(10),
    iva_euro DECIMAL(12,2) DEFAULT 0.00,
    
    -- Intelligenza Artificiale e Categorizzazione
    categoria_contabile VARCHAR(100),
    codice_conto VARCHAR(50),
    descrizione TEXT,
    note_cliente TEXT,
    
    -- Files e Workflow
    nuovo_nome_file VARCHAR(255),
    file_path VARCHAR(255),
    richiede_xml BOOLEAN DEFAULT FALSE,
    stato VARCHAR(50) DEFAULT 'caricato',
    
    -- Sicurezza e Affidabilità AI
    richiede_verifica BOOLEAN DEFAULT FALSE,
    confidence_score INT DEFAULT 0,
    
    data_inserimento TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. TABELLA: Log Attività (Audit Trail per la sicurezza e la conformità)
CREATE TABLE IF NOT EXISTS log_attivita (
    id INT AUTO_INCREMENT PRIMARY KEY,
    studio_id VARCHAR(50),
    utente_id VARCHAR(100),
    documento_id INT NULL,
    azione VARCHAR(255) NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS consumi_ai (
    id INT AUTO_INCREMENT PRIMARY KEY,
    studio_id VARCHAR(50),
    azienda VARCHAR(255),
    mese_anno VARCHAR(7),
    documenti_processati INT DEFAULT 0,
    pagine_processate INT DEFAULT 0,
    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY unique_consumo (studio_id, azienda, mese_anno)
);


CREATE TABLE IF NOT EXISTS processing_queue (
    id INT AUTO_INCREMENT PRIMARY KEY,
    batch_id VARCHAR(50) NOT NULL,
    studio_id VARCHAR(50) NOT NULL,
    utente_id VARCHAR(50) NOT NULL,
    azienda VARCHAR(100) NOT NULL,
    file_path TEXT NOT NULL,
    nome_originale VARCHAR(255) NOT NULL,
    stato ENUM('in_coda', 'in_elaborazione', 'completato', 'errore') DEFAULT 'in_coda',
    tentativi INT DEFAULT 0,
    errore TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP NULL DEFAULT NULL,
    finished_at TIMESTAMP NULL DEFAULT NULL,
    
    INDEX idx_polling (stato, created_at),
    INDEX idx_batch_ui (studio_id, azienda, batch_id)
);

-- ==========================================================
-- CREAZIONE ACCOUNT SUPER-ADMIN (Primo Accesso)
-- ==========================================================
-- Inseriamo la password "admin" in chiaro. 
-- Al primo accesso l'app la trasformerà automaticamente in Hash (Bcrypt).
INSERT IGNORE INTO utenti (username, password, studio_id, ruolo, nome_azienda) 
VALUES ('admin', 'admin', 'MASTER', 'admin', NULL);