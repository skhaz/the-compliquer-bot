import asyncio
import os
import subprocess
from contextlib import contextmanager
from tempfile import TemporaryDirectory

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from telegram import Update
from telegram.ext import Application
from telegram.ext import CommandHandler
from telegram.ext import ContextTypes
from wasmtime import Engine
from wasmtime import ExitTrap
from wasmtime import Func
from wasmtime import Linker
from wasmtime import Module
from wasmtime import Store
from wasmtime import WasiConfig


@contextmanager
def directory(path):
    original_dir = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(original_dir)


def run(source: str) -> str:
    with TemporaryDirectory() as path:
        with directory(path):
            with (
                open("main.cpp", "w+t") as main,
                open("stdout.txt", "w+t") as stdout,
                open("stderr.txt", "w+t") as stderr,
            ):
                main.write(source)
                main.flush()

                command = [
                    "emcc",
                    "-O3",
                    "-flto",
                    "-s",
                    "ENVIRONMENT=node",
                    "-s",
                    "PURE_WASI=1",
                    "-s",
                    "WASM=1",
                    "main.cpp",
                ]

                result = subprocess.run(command, capture_output=True, text=True)

                if result.returncode != 0:
                    raise Exception(result.stderr)

                with open("a.out.wasm", "rb") as binary:
                    wasi = WasiConfig()
                    wasi.stdout_file = "stdout.txt"
                    wasi.stderr_file = "stderr.txt"

                    engine = Engine()
                    store = Store(engine)
                    store.set_wasi(wasi)
                    linker = Linker(engine)
                    linker.define_wasi()
                    module = Module(store.engine, binary.read())
                    instance = linker.instantiate(store, module)
                    start = instance.exports(store)["_start"]
                    assert isinstance(start, Func)

                    try:
                        start(store)
                    except ExitTrap as e:
                        if e.code != 0:
                            raise Exception("exit code is not 0")

                    stdout.seek(0)
                    stderr.seek(0)
                    return f"stdout: {stdout.read()}\nstderr: {stderr.read()}"


def equals(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False

    if len(left) != len(right):
        return False

    for c1, c2 in zip(left, right):
        if c1 != c2:
            return False

    return True


async def on_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message:
        return

    text = message.text
    if not text:
        await message.reply_text("not text.")
        return

    text = text.lstrip("/run")

    if not text:
        await message.reply_text("Please provide a source code.")
        return

    loop = asyncio.get_event_loop()

    try:
        result = await loop.run_in_executor(None, run, text)
        await message.reply_text(result)
    except Exception as exc:
        await message.reply_text(f"{exc}")
        return


async def webhook(request: Request):
    if not equals(
        request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
        os.environ["SECRET"],
    ):
        return Response(content="Unauthorized", status_code=401)

    payload = await request.json()

    async with application:
        await application.process_update(Update.de_json(payload, application.bot))

    return Response(status_code=200)


application = (
    Application.builder().token(os.environ["TELEGRAM_TOKEN"]).updater(None).build()
)

application.add_handler(CommandHandler("run", on_run))

app = Starlette(
    debug=True,
    routes=[
        Route("/", webhook, methods=["POST"]),
    ],
)
