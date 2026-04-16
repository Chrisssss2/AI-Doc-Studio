# 📑 AI Doc Studio: Intelligent B2B SaaS for Accounting Automation

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python)
![Streamlit](https://img.shields.io/badge/Frontend-Streamlit-FF4B4B?style=for-the-badge&logo=streamlit)
![MySQL](https://img.shields.io/badge/Database-MySQL-4479A1?style=for-the-badge&logo=mysql)
![Gemini](https://img.shields.io/badge/AI-Google%20Gemini%201.5-orange?style=for-the-badge&logo=google-gemini)

**AI Doc Studio** è una piattaforma SaaS B2B progettata per rivoluzionare il workflow degli studi contabili. Automatizza l'intero ciclo di vita del documento: dall'upload dell'immagine/PDF alla generazione di file **XML conformi agli standard SDI**.

---

## 💡 Il Problema
La gestione manuale delle fatture e dei documenti contabili rappresenta un collo di bottiglia critico per gli studi professionali italiani. AI Doc Studio elimina l'errore umano e i tempi morti grazie a un'estrazione dati intelligente basata su modelli multimodali.

## ✨ Caratteristiche Tecniche Principali

### 🧠 **Intelligenza Artificiale Generativa**
* **Deep Extraction:** Utilizza `Google Gemini API` per analizzare PDF e immagini, estraendo non solo i totali, ma anche metadati complessi come Partite IVA, codici fiscali e categorie contabili.
* **Data Validation:** Logica di sanificazione dei dati (`sanitize_input`) e pulizia automatica dei codici fiscali/PIVA per garantire l'integrità del database.

### 🏗️ **Architettura ad Alte Prestazioni**
* **Asynchronous Worker:** Sistema basato su code di lavoro (`processing_queue`). Un processo worker dedicato gestisce l'elaborazione intensiva dei documenti in background, lasciando la UI fluida e reattiva.
* **Multi-tenant Ready:** Struttura database SQL scalabile che separa logicamente Studi Commercialisti, Utenti e Aziende gestite.
* **File Persistence:** Gestione sicura dei file con generazione di nomi univoci (`uuid`) e sanificazione dei nomi file originali per la compatibilità cross-platform.
* **PWA Transformation:** Grazie a uno script di patching personalizzato (`patch.py`), l'interfaccia Streamlit viene convertita in una **Progressive Web App** con icone custom e modalità offline supportata.
