# RemoteNode - Guia Paso a Paso (Espanol)

Esta guia explica como **instalar, configurar y usar** RemoteNode en Linux, de forma simple.

## 1) Requisitos

- Python 3.10+ (recomendado: `python3`)
- `tmux` instalado
- Cuenta de Telegram
- Token de bot de Telegram (desde [@BotFather](https://t.me/BotFather))
- Tu ID de Telegram (desde [@userinfobot](https://t.me/userinfobot))

## 2) Instalacion

En la carpeta del proyecto:

```bash
cd ./RemoteNode
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Instala tmux si no lo tienes:

```bash
# Arch
sudo pacman -S tmux

# Debian/Ubuntu
sudo apt install tmux
```

## 3) Configuracion

1. Crea el archivo de entorno:

```bash
cp .env.example .env
```

2. Edita `.env` y define:

```env
TELEGRAM_BOT_TOKEN=tu_token_aqui
ALLOWED_USER_IDS=tu_id_telegram
```

Notas:
- Si quieres varios usuarios, usa coma: `ALLOWED_USER_IDS=12345,67890`
- No subas `.env` al repositorio.

## 4) Iniciar el bot

```bash
cd ./RemoteNode
source .venv/bin/activate
python bot.py
```

## 5) Flujo de uso (movil + PC)

### 5.1 Abrir sesion remota desde Telegram

Ejemplo para Gemini:

```text
/open term gemini
```

Ejemplo para Claude:

```text
/open term claude
```

### 5.2 Ver la misma terminal en tu PC

En la PC:

```bash
tmux attach -t rnode_term
```

Eso abre **la misma sesion** que controlas desde Telegram.

### 5.3 Enviar comandos/mensajes

- Si estas adjunto (`/attach term`), cada mensaje normal en Telegram se envia a la terminal.
- El bot manda el envio con `Enter` automaticamente.
- La respuesta vuelve al movil con retardo (5 segundos por configuracion actual).

## 6) Comandos utiles de Telegram

- `/open <label> <cmd>`: crea sesion y ejecuta comando
- `/attach <label>`: te adjunta a una sesion
- `/detach`: dejas de reenviar texto
- `/peek <label>`: ver salida actual
- `/send <label> <texto>`: enviar texto sin adjuntarte
- `/key <label> C-c`: enviar teclas especiales
- `/sessions`: listar sesiones activas
- `/close <label>`: cerrar sesion tmux

## 7) Cerrar todo

### 7.1 Apagar bot

```bash
pkill -f "^python bot.py$"
```

### 7.2 Cerrar sesion tmux remota

```bash
tmux kill-session -t rnode_term
```

## 8) Problemas comunes

### Error `409 Conflict`

Significa que hay mas de una instancia del bot corriendo.

Solucion:

```bash
pkill -f "^python bot.py$"
cd ./RemoteNode
source .venv/bin/activate
python bot.py
```

### El texto se escribe pero no responde

- Verifica que estas en la sesion correcta: `/attach term`
- Verifica que Gemini/Claude esta realmente abierto en `tmux`
- En PC, confirma con:

```bash
tmux attach -t rnode_term
```

### No llegan mensajes al movil

- Revisa que el bot siga activo:

```bash
pgrep -af "^python bot.py$"
```

- Revisa logs:

```bash
tail -f ./remotenode.log
```

