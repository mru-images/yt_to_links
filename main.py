from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import yt_dlp
import os
import uuid
import requests
from io import BytesIO
import base64
import tempfile
import re
import json

app = FastAPI()

# Folder names in pCloud
SONGS_FOLDER = "songs"
IMGS_FOLDER = "imgs"

# Read env vars (set manually or through .env in local)
PCLOUD_AUTH_TOKEN = os.getenv("PCLOUD_AUTH_TOKEN")
YOUTUBE_COOKIES_BASE64 = os.getenv("YOUTUBE_COOKIES")


# ------------------ Helper Functions ------------------

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def get_or_create_folder(auth_token, folder_name):
    res = requests.get("https://api.pcloud.com/listfolder", params={
        "auth": auth_token, "folderid": 0
    }).json()
    for item in res.get("metadata", {}).get("contents", []):
        if item.get("isfolder") and item.get("name") == folder_name:
            return item["folderid"]
    res = requests.get("https://api.pcloud.com/createfolder", params={
        "auth": auth_token, "name": folder_name, "folderid": 0
    }).json()
    return res["metadata"]["folderid"]


def download_audio_and_thumbnail(url):
    buffer = BytesIO()
    temp_id = str(uuid.uuid4())

    # Decode base64 YouTube cookies
    cookies_text = base64.b64decode(YOUTUBE_COOKIES_BASE64).decode()
    with tempfile.NamedTemporaryFile(delete=False, mode='w', suffix=".txt") as cfile:
        cfile.write(cookies_text)
        cookie_path = cfile.name

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{temp_id}.%(ext)s",
        "cookiefile": cookie_path,
        "quiet": True,
        "noplaylist": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192"
        }]
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = sanitize_filename(info.get("title") or temp_id)
        thumb_url = info.get("thumbnail")
        mp3_path = f"{temp_id}.mp3"
        mp3_name = f"{title}.mp3"

    with open(mp3_path, 'rb') as f:
        buffer.write(f.read())
        buffer.seek(0)

    os.remove(mp3_path)
    os.remove(cookie_path)
    return buffer, mp3_name, thumb_url


def download_thumbnail(url):
    res = requests.get(url)
    if res.ok:
        return BytesIO(res.content), f"{uuid.uuid4()}.jpg"
    raise Exception("Thumbnail download failed")


def upload_to_pcloud(auth_token, folder_id, file_buffer, filename):
    file_buffer.seek(0)
    res = requests.post("https://api.pcloud.com/uploadfile", params={
        "auth": auth_token, "folderid": folder_id
    }, files={"file": (filename, file_buffer)}).json()
    if res["result"] != 0:
        raise Exception(f"Upload failed: {res}")
    fileid = res["metadata"][0]["fileid"]
    return fileid


def get_direct_public_link(auth_token, fileid):
    publink_res = requests.get("https://api.pcloud.com/getfilepublink", params={
        "auth": auth_token, "fileid": fileid
    }).json()
    if publink_res["result"] != 0:
        raise Exception(f"getfilepublink failed: {publink_res}")

    code = publink_res.get("code")
    if not code:
        raise Exception(f"getfilepublink returned no code: {publink_res}")

    showlink_res = requests.get("https://api.pcloud.com/showpublink", params={
        "code": code
    }).json()
    if showlink_res["result"] != 0:
        raise Exception(f"showpublink failed: {showlink_res}")

    metadata = showlink_res["metadata"]
    if metadata.get("isfolder") is False and "path" in metadata:
        file_path = metadata["path"]
    elif "contents" in metadata and len(metadata["contents"]) > 0:
        file_path = metadata["contents"][0]["path"]
    else:
        raise Exception("Could not extract file path from showpublink")

    return showlink_res["hosts"][0] + file_path


# ------------------ FastAPI Routes ------------------

@app.get("/")
def root():
    return {"message": "✅ YouTube → pCloud uploader is live!"}


@app.get("/upload")
def upload(link: str = Query(..., description="YouTube video URL")):
    try:
        if not PCLOUD_AUTH_TOKEN:
            raise Exception("Missing PCLOUD_AUTH_TOKEN")
        if not YOUTUBE_COOKIES_BASE64:
            raise Exception("Missing YOUTUBE_COOKIES")

        song_fid = get_or_create_folder(PCLOUD_AUTH_TOKEN, SONGS_FOLDER)
        img_fid = get_or_create_folder(PCLOUD_AUTH_TOKEN, IMGS_FOLDER)

        audio_buffer, mp3_name, thumb_url = download_audio_and_thumbnail(link)
        audio_fid = upload_to_pcloud(PCLOUD_AUTH_TOKEN, song_fid, audio_buffer, mp3_name)
        audio_buffer.close()
        mp3_url = get_direct_public_link(PCLOUD_AUTH_TOKEN, audio_fid)

        thumb_buffer, thumb_name = download_thumbnail(thumb_url)
        thumb_fid = upload_to_pcloud(PCLOUD_AUTH_TOKEN, img_fid, thumb_buffer, thumb_name)
        thumb_buffer.close()
        thumb_url = get_direct_public_link(PCLOUD_AUTH_TOKEN, thumb_fid)

        return JSONResponse({
            "mp3_url": mp3_url,
            "thumbnail_url": thumb_url,
            "file_name": mp3_name
        })

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
