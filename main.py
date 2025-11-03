import os
import time
import logging
import uuid
from typing import List, Dict, Optional
from pathlib import Path

import aiofiles
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Configuración de logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("asistec")

# Crear aplicación FastAPI
app = FastAPI(title="ASISTEC", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, restringir a orígenes de confianza
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# main.py (parte superior)
from typing import Optional
import os

# opcional: carga .env en desarrollo (instala python-dotenv si no lo tienes)
try:
    from dotenv import load_dotenv
    load_dotenv()  # cargará .env si existe
except Exception:
    pass

GOOGLE_API_KEY: Optional[str] = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID: Optional[str] = os.getenv("GOOGLE_CSE_ID")

logger.info(f"GOOGLE_API_KEY set? {'YES' if GOOGLE_API_KEY else 'NO'}; GOOGLE_CSE_ID set? {'YES' if GOOGLE_CSE_ID else 'NO'}")

# Directorio para subidas
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_EXT = {".txt", ".pdf", ".docx", ".csv", ".xlsx", ".json", ".png", ".jpg", ".jpeg", ".webp"}
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# Helper para búsqueda en Google
async def google_search(query: str, max_results: int = 3) -> str:
    if not (GOOGLE_API_KEY and GOOGLE_CSE_ID):
        return "[Búsqueda deshabilitada: falta GOOGLE_API_KEY o GOOGLE_CSE_ID]"
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": max_results}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.exception("Error en google_search:")
        return f"[Error en búsqueda: {e}]"
    items = data.get("items", [])
    if not items:
        return "[Sin resultados en Google]"
    out = []
    for i, it in enumerate(items, 1):
        out.append(f"{i}. {it.get('title', '')}\n{it.get('snippet', '')}\n{it.get('link', '')}\n")
    return "\n".join(out)

# Manejador de conexiones WebSocket
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.memories: Dict[str, List[str]] = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

manager = ConnectionManager()

# Endpoint WebSocket
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.connect(websocket)
    logger.info(f"Cliente conectado: {client_id}")
    manager.memories.setdefault(client_id, [])
    try:
        while True:
            try:
                data = await websocket.receive_text()
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.exception("Error leyendo mensaje del websocket:")
                await manager.send_personal_message(f"Error al recibir mensaje: {e}", websocket)
                continue

            logger.info(f"Recibido de {client_id}: {data!r}")

            # Obtener resultados de Google
            search_result = await google_search(data)

            # Formatear respuesta con resultados de Google
            response = f"Resultados de Google:\n{search_result}"

            # Guardar conversación y enviar respuesta
            manager.memories[client_id].append(f"Usuario: {data}")
            manager.memories[client_id].append(f"ASISTEC: {response}")

            try:
                await manager.send_personal_message(response, websocket)
            except Exception:
                logger.exception("Error enviando mensaje al websocket; desconectando cliente.")
                manager.disconnect(websocket)
                break

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        manager.memories.pop(client_id, None)
        logger.info(f"Cliente desconectado: {client_id}")
    except Exception:
        logger.exception("Excepción no controlada en websocket_endpoint")
        try:
            manager.disconnect(websocket)
            manager.memories.pop(client_id, None)
        except Exception:
            pass

# Endpoint para subir archivos
@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="File type not allowed")
    uid_name = f"{uuid.uuid4().hex}{ext}"
    path = UPLOAD_DIR / uid_name
    try:
        async with aiofiles.open(path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                await f.write(chunk)
    except Exception:
        logger.exception("Error guardando archivo subido")
        raise HTTPException(status_code=500, detail="Error saving file")
    return {"file_url": f"/uploads/{uid_name}", "filename": file.filename}

# Health check
@app.get("/")
def read_root():
    return {"message": "ASISTEC backend is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)