# -*- coding: utf-8 -*-
import streamlit as st
import os
import json
import re
from PIL import Image

# Trova la cartella principale di Streamlit
st_dir = os.path.dirname(st.__file__)
static_dir = os.path.join(st_dir, 'static')
index_path = os.path.join(static_dir, 'index.html')
manifest_path = os.path.join(static_dir, 'manifest.json')
logo_dest_path = os.path.join(static_dir, 'logo_app.png')

# 1. Elaborazione Grafica: Fonde il logo trasparente con lo sfondo scuro nativo
if os.path.exists("logo1.png"):
    try:
        # Carica il logo trasparente
        img = Image.open("logo1.png").convert("RGBA")
        # Crea una tela del colore esatto dell'app (#0e1117 -> RGB: 14, 17, 23)
        sfondo = Image.new("RGBA", img.size, (14, 17, 23, 255))
        # Incolla il logo sopra la tela scura
        sfondo.paste(img, (0, 0), img)
        # Rimuove definitivamente la trasparenza residua e salva
        sfondo.convert("RGB").save(logo_dest_path, "PNG")
        print("✅ Logo elaborato! Lo sfondo bianco è stato sconfitto.")
    except Exception as e:
        print(f"⚠️ Errore grafico: {e}")
else:
    print("⚠️ ATTENZIONE: File 'logo1.png' non trovato.")

# Da ora in poi usiamo il file fuso con lo sfondo
LINK_LOGO = "./logo_app.png"

# 2. Creiamo il vero Manifest
manifest_data = {
    "name": "AI Doc Studio",
    "short_name": "AI Doc Studio",
    "start_url": "./",
    "display": "standalone",
    "background_color": "#0e1117",
    "theme_color": "#0e1117",
    "icons": [
        {
            "src": LINK_LOGO,
            "sizes": "192x192",
            "type": "image/png"
        },
        {
            "src": LINK_LOGO,
            "sizes": "500x500",
            "type": "image/png"
        }
    ]
}

with open(manifest_path, 'w', encoding='utf-8') as f:
    json.dump(manifest_data, f, indent=4)

# 3. Aggiorniamo index.html FORZATAMENTE
tags_da_iniettare = f"""
    <link rel="manifest" href="./manifest.json">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="AI Doc Studio">
    <meta name="application-name" content="AI Doc Studio">
    <meta name="theme-color" content="#0e1117">
    <link rel="apple-touch-icon" href="{LINK_LOGO}">
    <title>AI Doc Studio</title>
    <style> body, html, .stApp {{ background-color: #0e1117 !important; }} </style>
"""

try:
    with open(index_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # PULIZIA FORZATA: Rimuoviamo il vecchio blocco
    html = re.sub(r'<link rel="manifest".*?</style>', '', html, flags=re.DOTALL)
    html = re.sub(r'<link rel="apple-touch-icon".*?>', '', html)
    html = re.sub(r'<title>.*?</title>', '', html)

    # Re-iniettiamo il codice pulito
    html = html.replace('<head>', f'<head>\n{tags_da_iniettare}')
    
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html)
        
    print("✅ PATCH COMPLETATA! L'App è pronta.")

except Exception as e:
    print(f"Errore: {e}")