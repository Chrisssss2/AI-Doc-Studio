# 🚀 AI Doc Studio

SaaS B2B per l'automazione del workflow contabile tramite Intelligenza Artificiale Generativa. 

### 💡 Il Problema
La burocrazia italiana e la gestione manuale delle fatture rallentano gli studi professionali. AI Doc Studio elimina l'inserimento manuale dei dati.

### 🛠️ Core Stack & Features
- **AI Engine:** Integrazione con Google Gemini per l'estrazione intelligente dei dati da PDF/Immagini.
- **Backend:** Python con processamento asincrono (Worker dedicato) per gestire carichi elevati.
- **Database:** MySQL per la gestione multi-tenant (Studi/Aziende).
- **Frontend:** Streamlit trasformato in PWA tramite patching personalizzato.

### ⚙️ Architettura
Il progetto utilizza un'architettura a coda:
1. L'utente carica i documenti.
2. Il sistema inserisce i job in una `processing_queue`.
3. Un **Worker indipendente** processa i file in background per massimizzare la reattività dell'interfaccia.
