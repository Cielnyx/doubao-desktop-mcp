import io
from pathlib import Path
import socket
import struct
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from doubao_mcp.downloads import DownloadError, _checked_url, _open_resource, download_video, read_mp4_metadata


def box(kind, payload):
    return struct.pack(">I4s",len(payload)+8,kind)+payload


def sample_mp4(width=720,height=1280,duration=4,version=0):
    if version == 0:
        clock = b"\0"*12+struct.pack(">II",1000,duration*1000)
        track = b"\0"*76+struct.pack(">II",width<<16,height<<16)
    else:
        clock = b"\1"+b"\0"*19+struct.pack(">IQ",1000,duration*1000)
        track = b"\1"+b"\0"*87+struct.pack(">II",width<<16,height<<16)
    mdia = box(b"mdia",box(b"mdhd",clock)+box(b"hdlr",b"\0"*8+b"vide"+b"\0"*12))
    moov = box(b"moov",box(b"mvhd",clock)+box(b"trak",box(b"tkhd",track)+mdia))
    return box(b"ftyp",b"isom\0\0\0\0isommp42")+box(b"mdat",b"video fixture bytes")+moov


class Response(io.BytesIO):
    def __init__(self,data,headers=None):
        super().__init__(data)
        self.headers={"Content-Type":"video/mp4","Content-Length":str(len(data)),**(headers or {})}
    def getheader(self,key,default=None):
        return self.headers.get(key,default)


class Connection:
    sock=None
    def close(self):
        pass


class DownloadsTests(unittest.TestCase):
    def test_real_container_metadata_versions_zero_and_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"sample.mp4"
            for version in (0,1):
                path.write_bytes(sample_mp4(version=version))
                info=read_mp4_metadata(path)
                self.assertEqual((info["duration"],info["width"],info["height"]),(4,720,1280))

    def test_non_video_and_truncated_containers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"invalid.mp4"
            for payload in (b"<html>expired</html>",sample_mp4()[:-3],sample_mp4().replace(b"vide",b"soun"),sample_mp4().replace(b"mdat",b"free")):
                path.write_bytes(payload)
                with self.assertRaises(DownloadError):
                    read_mp4_metadata(path)

    def test_movie_timeline_duration_keeps_video_track_duration_separate(self):
        original=box(b"mvhd",b"\0"*12+struct.pack(">II",1000,4000))
        padded=box(b"mvhd",b"\0"*12+struct.pack(">II",1000,4065))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"padding.mp4"
            path.write_bytes(sample_mp4().replace(original,padded))
            metadata=read_mp4_metadata(path)
        self.assertEqual(metadata["duration"],4.065)
        self.assertEqual(metadata["video_track_duration"],4)
        self.assertEqual(metadata["duration_source"],"movie_header")

    def test_private_dns_results_and_untrusted_url_schemes_are_rejected(self):
        for address in ("127.0.0.1","10.1.2.3","169.254.169.254","::1","fc00::1"):
            with patch("doubao_mcp.downloads.socket.getaddrinfo",return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,"",(address,443))]):
                with self.subTest(address=address),self.assertRaises(DownloadError) as error:
                    _checked_url("https://media.example/video.mp4",time.monotonic()+5)
                self.assertEqual(error.exception.code,"UNSAFE_VIDEO_URL")
        for url in ("http://cdn.example/x.mp4","file:///x.mp4","https://user:secret@cdn.example/x.mp4","https://cdn.example:8443/x.mp4","https://localhost/x.mp4"):
            with patch("doubao_mcp.downloads.socket.getaddrinfo") as resolver:
                with self.subTest(url=url),self.assertRaises(DownloadError):
                    _checked_url(url,time.monotonic()+5)
                resolver.assert_not_called()

    def test_redirect_to_private_host_is_checked_before_connecting(self):
        public=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))]
        private=[(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))]
        connection=Mock()
        response=Response(b"",{"Location":"https://redirect.example/private"})
        response.status=302
        connection.getresponse.return_value=response
        with patch("doubao_mcp.downloads.socket.getaddrinfo",side_effect=[public,private]),patch("doubao_mcp.downloads._PinnedHTTPSConnection",return_value=connection) as factory:
            with self.assertRaises(DownloadError) as error:
                _open_resource("https://source.example/video",time.monotonic()+5)
            self.assertEqual(error.exception.code,"UNSAFE_VIDEO_URL")
            self.assertEqual(factory.call_count,1)
            connection.close.assert_called_once()

    def test_download_reports_measured_metadata_and_never_overwrites(self):
        data=sample_mp4()
        with tempfile.TemporaryDirectory() as directory:
            with patch("doubao_mcp.downloads._open_resource",return_value=(Connection(),Response(data))):
                result=download_video("https://source.example/video",directory,"result.mp4",{"duration":5,"width":720,"height":1280,"expected_bytes":len(data)})
            self.assertTrue(result["downloaded"] and result["metadata_verified"])
            self.assertEqual(result["duration"],4)
            self.assertEqual(result["metadata_mismatches"],{"duration":{"expected":5,"actual":4}})
            self.assertEqual(Path(result["path"]).read_bytes(),data)
            with patch("doubao_mcp.downloads._open_resource") as network:
                with self.assertRaises(DownloadError) as error:
                    download_video("https://source.example/video",directory,"result.mp4")
                self.assertEqual(error.exception.code,"DESTINATION_EXISTS")
                network.assert_not_called()

    def test_incomplete_and_oversize_downloads_leave_no_partial_files(self):
        data=sample_mp4()
        with tempfile.TemporaryDirectory() as directory:
            for headers,limit in (({"Content-Length":str(len(data)+1)},10000),({"Content-Length":str(len(data))},10)):
                with patch("doubao_mcp.downloads._open_resource",return_value=(Connection(),Response(data,headers))):
                    with self.assertRaises(DownloadError):
                        download_video("https://source.example/video",directory,"failed.mp4",max_bytes=limit)
                self.assertEqual(list(Path(directory).iterdir()),[])

    def test_byte_limit_also_applies_without_content_length(self):
        response=Response(sample_mp4())
        response.headers.pop("Content-Length")
        with tempfile.TemporaryDirectory() as directory:
            with patch("doubao_mcp.downloads._open_resource",return_value=(Connection(),response)):
                with self.assertRaises(DownloadError) as error:
                    download_video("https://source.example/video",directory,max_bytes=10)
                self.assertEqual(error.exception.code,"DOWNLOAD_TOO_LARGE")
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_unsafe_filenames_fail_before_any_network_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            for filename in ("../x.mp4","sub\\x.mp4","CON.mp4","CON .mp4","COM¹.mp4","result.exe","name.mp4 ","a:b.mp4"):
                with patch("doubao_mcp.downloads._open_resource") as network:
                    with self.subTest(filename=filename),self.assertRaises(DownloadError):
                        download_video("https://source.example/video",directory,filename)
                    network.assert_not_called()

    def test_existing_target_created_during_download_is_preserved(self):
        data=sample_mp4()
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/"race.mp4"
            def network(*args):
                target.write_bytes(b"another writer")
                return Connection(),Response(data)
            with patch("doubao_mcp.downloads._open_resource",side_effect=network):
                with self.assertRaises(DownloadError) as error:
                    download_video("https://source.example/video",directory,"race.mp4")
                self.assertEqual(error.exception.code,"DESTINATION_EXISTS")
            self.assertEqual(target.read_bytes(),b"another writer")
            self.assertEqual(list(Path(directory).iterdir()),[target])

    def test_network_timeout_removes_our_partial_download(self):
        response=Response(sample_mp4())
        response.read1=Mock(side_effect=[b"some bytes",TimeoutError("socket timed out")])
        with tempfile.TemporaryDirectory() as directory:
            with patch("doubao_mcp.downloads._open_resource",return_value=(Connection(),response)):
                with self.assertRaises(DownloadError) as error:
                    download_video("https://source.example/video",directory,"timed-out.mp4")
                self.assertEqual(error.exception.code,"DOWNLOAD_TIMEOUT")
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_collision_with_unowned_partial_file_does_not_delete_it(self):
        data=sample_mp4()
        with tempfile.TemporaryDirectory() as directory:
            existing=Path(directory)/".doubao-video-fixed.part"
            existing.write_bytes(b"not ours")
            with patch("doubao_mcp.downloads.uuid.uuid4",return_value=Mock(hex="fixed")),patch("doubao_mcp.downloads._open_resource",return_value=(Connection(),Response(data))):
                with self.assertRaises(DownloadError):
                    download_video("https://source.example/video",directory,"result.mp4")
            self.assertEqual(existing.read_bytes(),b"not ours")
            self.assertEqual(list(Path(directory).iterdir()),[existing])


if __name__ == "__main__":
    unittest.main()
