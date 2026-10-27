"""One operation at a time, including across multiple MCP server instances."""
from contextlib import contextmanager
from pathlib import Path
import os


@contextmanager
def desktop_lock():
    import msvcrt

    path = Path(os.environ.get("DOUBAO_LOCK_DIR", Path(__file__).resolve().parents[2] / ".runtime"))
    path.mkdir(parents=True, exist_ok=True)
    with (path / "desktop.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("豆包正由另一个 MCP 调用操作，请等待该调用结束。") from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)

