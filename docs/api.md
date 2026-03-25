# API Overview

Base URL: `/api/v1`

Nota: il viewer `trame` gira come applicazione web separata, ma la sua sessione può essere orchestrata tramite queste API.

## Endpoints

### `GET /health`

Restituisce lo stato dell’API.

### `GET /datasets`

Elenca i dataset disponibili.

Ogni dataset espone anche:

- `dataset_type`
- `origin`
- `uploaded`

### `POST /datasets/load`

Restituisce record e metadata di un dataset già registrato, utile per il viewer runtime.

Payload:

```json
{
  "dataset_id": "wallaby_upload_1"
}
```

### `GET /files/browser`

Esplora il filesystem remoto sotto `REMOTE_DATA_ROOT`.

Query param:

- `path`

La risposta contiene:

- `current_path`
- `parent_path`
- `entries`

Ogni entry indica:

- `directory`
- `fits`
- `other`

### `POST /datasets/load-path`

Carica un file FITS già presente sul server, validato rispetto a `REMOTE_DATA_ROOT`.

Payload:

```json
{
  "relative_path": "observations/wallaby/WALLABY.fits"
}
```

Il path viene normalizzato e bloccato se tenta di uscire da `REMOTE_DATA_ROOT`.

### `GET /datasets/{dataset_id}/metadata`

Restituisce metadati del dataset. Per FITS include:

- `shape`
- `naxis`
- `dtype_original`
- `header`
- statistiche finite-only

### `GET /datasets/{dataset_id}/fits-header`

Restituisce i principali header FITS per dataset di tipo `fits`.

### `POST /sessions`

Apre una sessione logica di rendering su un dataset.

Payload:

```json
{
  "dataset_id": "galaxy_points"
}
```

### `GET /sessions/{session_id}`

Restituisce i dettagli della sessione corrente.

### `POST /sessions/{session_id}/render`

Aggiorna parametri e genera un nuovo PNG renderizzato.

Payload esempio:

```json
{
  "color_by": "density",
  "colormap": "Viridis (matplotlib)",
  "opacity": 0.85,
  "point_size": 4.0,
  "camera": {
    "azimuth": 25,
    "elevation": 20,
    "zoom": 1.2
  },
  "filter": {
    "kind": "threshold",
    "enabled": true,
    "scalar_field": "density",
    "lower": 0.2,
    "upper": 0.9
  }
}
```

## Response Model Principale

Lo step di render restituisce:

- `session_id`
- `dataset_id`
- `image_url`
- `parameters`
- `rendered_at`

Questo endpoint appartiene alla modalità fallback non interattiva. L’architettura attiva privilegia invece le sessioni `pvserver` sotto `/interactive/sessions`.

## Interactive Session Endpoints

### `GET /interactive/sessions`

Elenca le sessioni `pvserver` note al session manager.

### `POST /interactive/sessions`

Crea una sessione interattiva.

Payload esempio locale:

```json
{
  "launch_mode": "local"
}
```

`dataset_id` è opzionale: il viewer può partire senza dataset e caricarne uno successivamente via UI o API, tipicamente tramite il browser remoto del filesystem server-side.

Payload esempio MPI:

```json
{
  "dataset_id": "wave_points",
  "launch_mode": "mpiexec",
  "nodes": 1,
  "ranks_per_node": 4
}
```

Payload esempio attach:

```json
{
  "dataset_id": "galaxy_points",
  "launch_mode": "attach",
  "host": "cluster-node.example",
  "port": 11111
}
```

### `GET /interactive/sessions/{session_id}`

Restituisce host, porta, dataset e metadata della sessione.

### `DELETE /interactive/sessions/{session_id}`

Ferma una sessione locale gestita dal session manager.
