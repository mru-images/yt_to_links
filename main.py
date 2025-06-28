from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import yt_dlp
import os
import uuid
import requests
from io import BytesIO
import tempfile
import base64
import traceback

app = FastAPI()

# 🔐 Use Render env vars
AUTH_TOKEN = os.getenv("PCLOUD_AUTH_TOKEN")
YOUTUBE_COOKIES_BASE64 = os.getenv("YOUTUBE_COOKIES")

SONGS_FOLDER = "songs"
IMGS_FOLDER = "imgs"

def write_temp_cookie_file():
    if not YOUTUBE_COOKIES_BASE64:
        raise Exception("YOUTUBE_COOKIES env var not set")
    cookie_bytes = base64.b64decode(YOUTUBE_COOKIES_BASE64)
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="wb")
    temp.write(cookie_bytes)
    temp.close()
    return temp.name

def get_or_create_folder(folder_name):
    res = requests.get("https://api.pcloud.com/listfolder", params={
        "auth": AUTH_TOKEN,
        "folderid": 0
    })
    data = res.json()
    for item in data.get("metadata", {}).get("contents", []):
        if item.get("isfolder") and item.get("name") == folder_name:
            return item["folderid"]

    res = requests.get("https://api.pcloud.com/createfolder", params={
        "auth": AUTH_TOKEN,
        "name": folder_name,
        "folderid": 0
    })
    data = res.json()
    return data["metadata"]["folderid"]

def upload_file(file_buffer, filename, folder_id):
    file_buffer.seek(0)
    res = requests.post("https://api.pcloud.com/uploadfile", params={
        "auth": AUTH_TOKEN,
        "folderid": folder_id
    }, files={
        "file": (filename, file_buffer)
    })

    if res.status_code != 200:
        raise Exception("Upload failed: HTTP " + str(res.status_code))

    data = res.json()
    if data.get("result") != 0:
        raise Exception(f"Upload failed: {data}")

    metadata = data["metadata"][0]
    fileid = metadata["fileid"]
    name = metadata["name"]

    # Safely make it public
    pub = requests.get("https://api.pcloud.com/getfilepublink", params={
        "auth": AUTH_TOKEN,
        "fileid": fileid
    })
    pub_data = pub.json()
    if pub_data.get("result") != 0:
        raise Exception(f"Failed to make file public: {pub_data}")

    return fileid, name

def download_audio_and_thumbnail(video_url, cookie_file_path):
    buffer = BytesIO()
    temp_id = str(uuid.uuid4())
    filename = f"{temp_id}.mp3"

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f"{temp_id}.%(ext)s",
        'quiet': True,
        'cookiefile': cookie_file_path,
        'noplaylist': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        full_path = f"{temp_id}.mp3"
        thumbnail_url = info.get("thumbnail")

    with open(full_path, 'rb') as f:
        buffer.write(f.read())
        buffer.seek(0)

    os.remove(full_path)
    return buffer, filename, thumbnail_url

def download_thumbnail(thumbnail_url):
    res = requests.get(thumbnail_url)
    if res.status_code == 200:
        buffer = BytesIO(res.content)
        filename = f"{uuid.uuid4()}.jpg"
        return buffer, filename
    raise Exception("Failed to download thumbnail")

@app.get("/")
def home():
    return {"message": "FastAPI YouTube to pCloud uploader is live on Render!"}

@app.get("/upload")
def upload(link: str = Query(..., description="YouTube video URL")):
    try:
        if not AUTH_TOKEN:
            raise Exception("PCLOUD_AUTH_TOKEN not set")

        cookie_path = write_temp_cookie_file()
        songs_folder_id = get_or_create_folder(SONGS_FOLDER)
        imgs_folder_id = get_or_create_folder(IMGS_FOLDER)

        audio_buffer, audio_filename, thumb_url = download_audio_and_thumbnail(link, cookie_path)
        thumb_buffer, thumb_filename = download_thumbnail(thumb_url)

        mp3_fileid, mp3_name = upload_file(audio_buffer, audio_filename, songs_folder_id)
        jpg_fileid, jpg_name = upload_file(thumb_buffer, thumb_filename, imgs_folder_id)

        audio_buffer.close()
        thumb_buffer.close()
        os.remove(cookie_path)

        return JSONResponse(content={
            "mp3": {
                "fileid": mp3_fileid,
                "filename": mp3_name
            },
            "thumbnail": {
                "fileid": jpg_fileid,
                "filename": jpg_name
            }
        })

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
