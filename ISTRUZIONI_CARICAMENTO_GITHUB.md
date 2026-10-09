# MINARDI PIUME · Pubblicazione QR Analytics

1. Estrarre questo ZIP sul PC.
2. Da account GitHub Yury-Minardi, aprire https://github.com/Yury-Minardi/minardi-piume-qr e fare clic su **uploading an existing file**.
3. Trascinare **i contenuti estratti**, non lo ZIP; includere le cartelle `assets` e `static` e i file `render.yaml`, `Dockerfile`, `main.py`, ecc.
4. Cliccare **Commit changes**.
5. Comunicarmi «File caricati». Procederò alla configurazione su Render.

**ATTENZIONE:** non caricare password, il file del database, credenziali aziendali o cartelle locali `data` e `.env`. Il pacchetto è già pulito.

Il piano Render indicato nel file `render.yaml` è `0.1c-256mb` con disco persistente di 1 GB, per un costo di listino di 6,25 USD/mese **al netto di IVA e consumi extra**. Le prestazioni della fascia 256 MB vanno monitorate; se la fattura prevista supera 8 EUR/mese, fermarsi e chiedere approvazione.

Nel Blueprint, la variabile `BOOTSTRAP_ADMIN_PASSWORD` dev'essere inserita nell'interfaccia dei segreti di Render, **mai su GitHub**. L'admin sarà `c.mascia@minardipiume.it`; la prima sessione richiederà cambio password.
