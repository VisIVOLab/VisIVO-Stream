# TODO

## Evoluzioni Architetturali

- Persistenza sessioni su Redis o PostgreSQL
- Coda job asincrona per render pesanti
- Gestione tenant e multiutente reale
- Autenticazione OAuth2/OIDC
- Policy di autorizzazione per dataset e progetti

## Rendering

- Supporto a dataset VTK/VTU/VTI/PLY oltre ai CSV di esempio
- Streaming interattivo via WebSocket
- Sessioni ParaView persistenti per ridurre latenza
- Export screenshot ad alta risoluzione e preset di camera
- Preset di filtri scientifici specifici per VisIVO

## Frontend

- Viewer interattivo con timeline delle operazioni
- Form per upload dataset
- Stato URL-driven e salvataggio workspace
- Gestione errori avanzata e retry UX

## Deployment

- Reverse proxy Nginx attivato in Compose
- TLS terminazione
- Metriche Prometheus
- Logging centralizzato
- Helm chart o stack Kubernetes

## HPC

- Submission job a Slurm
- Dataset remoti da object storage
- Catalogo metadati e versionamento

