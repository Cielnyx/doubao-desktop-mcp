"""Download a task-resolved video without copying browser credentials."""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urljoin, urlsplit
import uuid


class DownloadError(RuntimeError):
    def __init__(self, code, message, **details):
        details.setdefault("downloaded",False)
        self.code, self.details = code, details
        super().__init__(message)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DownloadError("DOWNLOAD_TIMEOUT", "视频下载超过时限。")
    return remaining


def _public_addresses(host, deadline):
    result = []
    ready = threading.Event()
    def resolve():
        try:
            result.append(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception as error:
            result.append(error)
        finally:
            ready.set()
    threading.Thread(target=resolve, daemon=True).start()
    if not ready.wait(_remaining(deadline)):
        raise DownloadError("DOWNLOAD_TIMEOUT", "视频域名解析超时。")
    if isinstance(result[0], Exception):
        raise DownloadError("DOWNLOAD_NETWORK_ERROR", "无法解析视频资源域名。") from result[0]
    addresses = list(dict.fromkeys(item[4][0] for item in result[0]))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise DownloadError("UNSAFE_VIDEO_URL", "拒绝连接非公网视频地址。")
    return addresses


def _checked_url(url, deadline):
    if not isinstance(url, str) or len(url) > 32768 or any(ord(c) < 32 for c in url):
        raise DownloadError("UNSAFE_VIDEO_URL", "视频地址格式不受支持。")
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None,443):
            raise ValueError("not public HTTPS")
        host = parsed.hostname.encode("idna").decode("ascii")
        if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local")):
            raise ValueError("local hostname")
    except (ValueError, UnicodeError) as error:
        raise DownloadError("UNSAFE_VIDEO_URL", "只接受任务解析出的公网 HTTPS 视频地址。") from error
    return parsed, host, _public_addresses(host, deadline)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect only to a previously checked IP while verifying the real TLS host."""
    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        raw = socket.create_connection((self._address,443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _open_resource(url, deadline):
    for redirects in range(6):
        parsed, host, addresses = _checked_url(url, deadline)
        connection = None
        last_error = None
        for address in addresses:
            connection = _PinnedHTTPSConnection(host, address, min(15,_remaining(deadline)))
            try:
                target = parsed.path or "/"
                if parsed.query:
                    target += "?" + parsed.query
                connection.request("GET", target, headers={"Accept":"video/*,application/octet-stream",
                    "Accept-Encoding":"identity", "User-Agent":"doubao-desktop-mcp", "Referer":"https://www.doubao.com/"})
                response = connection.getresponse()
                break
            except (OSError,http.client.HTTPException) as error:
                last_error = error
                connection.close()
        else:
            raise DownloadError("DOWNLOAD_NETWORK_ERROR", "无法连接视频资源服务器。") from last_error
        if response.status in (301,302,303,307,308):
            location = response.getheader("Location")
            response.close()
            connection.close()
            if redirects == 5 or not location:
                raise DownloadError("DOWNLOAD_REDIRECT_ERROR", "视频资源跳转次数过多或缺少目标地址。")
            url = urljoin(url, location)
            continue
        if response.status != 200:
            status = response.status
            response.close()
            connection.close()
            raise DownloadError("DOWNLOAD_HTTP_ERROR", f"视频资源服务器返回 HTTP {status}。", http_status=status)
        return connection, response
    raise AssertionError("unreachable")


def _safe_filename(filename):
    if filename is None:
        return "doubao-video-" + uuid.uuid4().hex[:16] + ".mp4"
    if (not isinstance(filename,str) or not 1 <= len(filename) <= 200 or
            re.search(r'[<>:"/\\|?*\x00-\x1f]',filename) or filename.endswith((" ",".")) or
            not filename.lower().endswith(".mp4") or filename.split(".",1)[0].rstrip(" .").upper() in
            {"CON","PRN","AUX","NUL","CONIN$","CONOUT$",*(f"COM{i}" for i in "0123456789¹²³"),*(f"LPT{i}" for i in "0123456789¹²³")}):
        raise DownloadError("INVALID_FILENAME", "filename 必须是安全的单个 .mp4 文件名，不能包含路径。")
    return filename


def _boxes(stream, start, end):
    position, count = start, 0
    while position < end:
        count += 1
        if count > 100000 or end-position < 8:
            raise DownloadError("INVALID_VIDEO_FILE", "视频容器结构不完整。")
        stream.seek(position)
        size, kind = struct.unpack(">I4s",stream.read(8))
        header = 8
        if size == 1:
            if end-position < 16:
                raise DownloadError("INVALID_VIDEO_FILE", "视频扩展容器头不完整。")
            size = struct.unpack(">Q",stream.read(8))[0]
            header = 16
        elif size == 0:
            size = end-position
        if size < header or position+size > end:
            raise DownloadError("INVALID_VIDEO_FILE", "视频容器长度无效。")
        yield kind, position+header, position+size
        position += size


def _time_header(stream, start, end):
    stream.seek(start)
    data = stream.read(min(32,end-start))
    if len(data) >= 20 and data[0] == 0:
        scale, duration = struct.unpack(">II",data[12:20])
        if duration == 0xffffffff:
            return None
    elif len(data) >= 32 and data[0] == 1:
        scale = struct.unpack(">I",data[20:24])[0]
        duration = struct.unpack(">Q",data[24:32])[0]
        if duration == 0xffffffffffffffff:
            return None
    else:
        return None
    return duration/scale if scale > 0 and duration > 0 else None


def read_mp4_metadata(path):
    """Read measured dimensions/duration from ISO BMFF headers, without ffprobe."""
    size = Path(path).stat().st_size
    ftyp, movie_duration, tracks, moov, media_data = False, None, [], False, False
    with Path(path).open("rb") as stream:
        for kind, start, end in _boxes(stream,0,size):
            if kind == b"ftyp":
                ftyp = end-start >= 8
            elif kind == b"mdat":
                media_data = media_data or end > start
            elif kind == b"moov":
                moov = True
                for child, first, last in _boxes(stream,start,end):
                    if child == b"mvhd":
                        movie_duration = _time_header(stream,first,last)
                    elif child == b"trak":
                        track = {"video":False,"width":None,"height":None,"duration":None}
                        for part, begin, finish in _boxes(stream,first,last):
                            if part == b"tkhd":
                                stream.seek(begin)
                                version = stream.read(1)
                                offset = 76 if version == b"\x00" else 88 if version == b"\x01" else None
                                if offset is not None and finish-begin >= offset+8:
                                    stream.seek(begin+offset)
                                    w,h = struct.unpack(">II",stream.read(8))
                                    track["width"],track["height"] = round(w/65536),round(h/65536)
                            elif part == b"mdia":
                                for member, a, b in _boxes(stream,begin,finish):
                                    if member == b"mdhd":
                                        track["duration"] = _time_header(stream,a,b)
                                    elif member == b"hdlr" and b-a >= 12:
                                        stream.seek(a+8)
                                        track["video"] = stream.read(4) == b"vide"
                        tracks.append(track)
    video_tracks = [t for t in tracks if t["video"]]
    if not ftyp or not moov or not media_data or not video_tracks:
        raise DownloadError("INVALID_VIDEO_FILE", "下载结果不是可识别的 MP4 视频，未保存成品。")
    selected = max(video_tracks,key=lambda t:(t["width"] or 0)*(t["height"] or 0))
    width,height = selected["width"],selected["height"]
    video_track_duration = selected["duration"]
    # Players use the movie timeline, which may include a little audio padding.
    # Keep the video-track duration separately instead of conflating the two.
    duration = movie_duration or video_track_duration
    return {"duration":round(duration,6) if duration else None,
            "width":width if width and width > 0 else None,"height":height if height and height > 0 else None,
            "metadata_source":"mp4_container",
            "duration_source":"movie_header" if movie_duration else "video_track_header",
            "video_track_duration":round(video_track_duration,6) if video_track_duration else None}


def _publish(temp, target):
    try:
        os.link(temp,target)
    except FileExistsError:
        raise DownloadError("DESTINATION_EXISTS", "目标文件已存在，未覆盖。")
    except OSError:
        created = False
        try:
            with target.open("xb") as output:
                created = True
                with temp.open("rb") as source:
                    shutil.copyfileobj(source,output,1024*1024)
        except FileExistsError:
            raise DownloadError("DESTINATION_EXISTS", "目标文件已存在，未覆盖。")
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
    temp.unlink()


def download_video(url, directory, filename=None, expected_metadata=None, timeout=120, max_bytes=1024**3):
    """Internal API: url must come from a real task, never a public MCP argument."""
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not 1 <= timeout <= 600:
        raise DownloadError("INVALID_TIMEOUT", "下载时限必须在 1–600 秒之间。")
    if type(max_bytes) is not int or not 1 <= max_bytes <= 4*1024**3:
        raise DownloadError("INVALID_SIZE_LIMIT", "视频下载大小限制无效。")
    if not isinstance(directory,(str,os.PathLike)) or not Path(directory).is_absolute():
        raise DownloadError("INVALID_OUTPUT_DIRECTORY", "输出目录必须是本地绝对路径。")
    name = _safe_filename(filename)
    target_dir = Path(directory).resolve()
    target = target_dir/name
    if target.exists():
        raise DownloadError("DESTINATION_EXISTS", "目标文件已存在，未覆盖。",path=str(target))
    deadline, temp, connection, response = time.monotonic()+timeout, None, None, None
    temp_created, failure = False, None
    try:
        connection,response = _open_resource(url,deadline)
        content_type = response.getheader("Content-Type","").split(";",1)[0].strip().lower()
        if content_type.startswith("text/") or content_type in ("application/json","application/xml"):
            raise DownloadError("INVALID_VIDEO_RESPONSE", "视频服务器返回了文本或错误页面。")
        length = response.getheader("Content-Length")
        try:
            announced = int(length) if length is not None else None
        except ValueError:
            raise DownloadError("INVALID_VIDEO_RESPONSE", "视频服务器返回了无效文件长度。")
        if announced is not None and (announced <= 0 or announced > max_bytes):
            raise DownloadError("DOWNLOAD_TOO_LARGE", "视频文件为空或超过下载大小限制。")
        target_dir.mkdir(parents=True,exist_ok=True)
        # tempfile.mkstemp retries PermissionError extensively on Windows.
        # A UUID plus exclusive creation fails promptly for unwritable folders.
        temp = target_dir/(".doubao-video-"+uuid.uuid4().hex+".part")
        digest,count = hashlib.sha256(),0
        with temp.open("xb") as output:
            temp_created = True
            while True:
                remaining = _remaining(deadline)
                if connection.sock is not None:
                    connection.sock.settimeout(min(15,remaining))
                chunk = response.read1(256*1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > max_bytes:
                    raise DownloadError("DOWNLOAD_TOO_LARGE", "视频文件超过下载大小限制。")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if not count or (announced is not None and count != announced):
            raise DownloadError("INCOMPLETE_DOWNLOAD", "视频下载字节数与服务器长度不一致。",bytes=count)
        expected = dict(expected_metadata or {})
        expected_size = expected.get("expected_bytes")
        if isinstance(expected_size,int) and not isinstance(expected_size,bool) and expected_size > 0 and count != expected_size:
            raise DownloadError("INCOMPLETE_DOWNLOAD", "下载大小与任务资源标注不一致。",bytes=count,expected_bytes=expected_size)
        metadata = read_mp4_metadata(temp)
        mismatches = {}
        for field in ("duration","width","height"):
            actual,wanted = metadata[field],expected.get(field)
            if actual is not None and isinstance(wanted,(int,float)) and not isinstance(wanted,bool) and wanted > 0:
                tolerance = 0.25 if field == "duration" else 0
                if abs(actual-wanted) > tolerance:
                    mismatches[field] = {"expected":wanted,"actual":actual}
        _remaining(deadline)
        _publish(temp,target)
        temp = None
        return {"ok":True,"downloaded":True,"path":str(target),"filename":name,"bytes":count,
                "sha256":digest.hexdigest(),**metadata,"metadata_verified":all(metadata[k] is not None for k in ("duration","width","height")),
                "metadata_mismatches":mismatches,"expected_metadata":expected}
    except DownloadError as error:
        failure = error
        raise
    except TimeoutError as error:
        failure = DownloadError("DOWNLOAD_TIMEOUT", "视频网络传输超过时限。")
        raise failure from error
    except (OSError,http.client.HTTPException,ValueError,struct.error) as error:
        failure = DownloadError("DOWNLOAD_FAILED", "视频下载或媒体文件读取失败。",reason=type(error).__name__)
        raise failure from error
    finally:
        if response is not None:
            try:
                response.close()
            except OSError:
                pass
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        if temp is not None and temp_created:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                if failure is not None:
                    failure.details.update(cleanup_failed=True,partial_path=str(temp))
