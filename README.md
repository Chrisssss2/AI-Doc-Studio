# 📑 AI Doc Studio: Intelligent B2B SaaS for Accounting Automation

![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python)
![Streamlit](https://img.shields.io/badge/Frontend-Streamlit-FF4B4B?style=for-the-badge&logo=streamlit)
![MySQL](https://img.shields.io/badge/Database-MySQL-4479A1?style=for-the-badge&logo=mysql)
![Gemini](https://img.shields.io/badge/AI-Google%20Gemini%201.5-orange?style=for-the-badge&logo=google-gemini)
![License](https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge)

**AI Doc Studio** è una piattaforma **SaaS B2B** avanzata progettata per rivoluzionare il workflow degli studi contabili italiani. Il sistema automatizza l'intero ciclo di vita del documento: dall'upload di PDF/Immagini alla generazione di file **XML conformi agli standard SDI**.

---

## 💡 Il Problema
La gestione manuale delle fatture e la burocrazia rallentano drasticamente la produttività professionale. AI Doc Studio elimina l'inserimento manuale dei dati utilizzando l'Intelligenza Artificiale Generativa per comprendere il contesto dei documenti, superando i limiti dei tradizionali sistemi OCR.

---

## ✨ Caratteristiche Principali

### 🧠 **AI Engine & Context Awareness**
* **Estrazione Multimodale:** Integrazione con **Google Gemini 1.5** per analizzare documenti complessi, distinguendo fornitore italiano ed estero.
* **Smart Categorization:** Categorizzazione automatica delle spese e gestione dei documenti multi-pagina.
* **Data Sanitization:** Algoritmi avanzati per la pulizia di P.IVA e Codici Fiscali (`sanitize_input`) per garantire l'integrità del database.

### 🏗️ **Architettura Scalabile (Worker-Based)**
* **Processing Queue:** Gestione dei carichi elevati tramite una coda di elaborazione asincrona (`processing_queue`).
* **Independent Worker:** Un modulo `worker.py` dedicato processa i file in background, massimizzando la reattività dell'interfaccia utente.
* **Multi-tenant Architecture:** Database MySQL strutturato per gestire separatamente Studi Commercialisti, Utenti e Aziende.

### 🔌 **Frontend & PWA Transformation**
* **PWA Ready:** Sistema di patching personalizzato (`patch.py`) per trasformare l'interfaccia Streamlit in una **Progressive Web App**, con icone custom e UX ottimizzata per mobile.
* **Security:** Gestione delle sessioni e autenticazione sicura tramite password hashing con `Bcrypt`.

---

## ⚙️ Workflow del Sistema

1.  **Ingestion:** L'utente effettua l'upload dei documenti tramite la console web.
2.  **Queuing:** Il sistema valida il file, genera un `UUID` univoco e inserisce un job nella coda.
3.  **Processing:** Il `worker.py` rileva il job, chiama l'IA, estrae i metadati e salva il risultato.
4.  **Finalization:** L'utente revisiona i dati ed esporta il file **XML SDI** pronto per l'invio.
