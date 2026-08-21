from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / ".external" / "playwright-sofifa-profile"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Abre Chromium con el perfil persistente que usa el actualizador SoFIFA."
    )
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE))
    parser.add_argument("--url", default="https://sofifa.com/my/settings")
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=0,
        help="Si es mayor a 0, espera esa cantidad de segundos y cierra guardando sesion sin pedir Enter.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=args.profile_dir,
            channel="chrome",
            headless=False,
            viewport={"width": 1400, "height": 1000},
            locale="es-ES",
            timezone_id="America/Argentina/Buenos_Aires",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(args.url, wait_until="domcontentloaded", timeout=30000)

        print("Chromium de Playwright abierto.")
        print("Inicia sesion en SoFIFA y completa Cloudflare si aparece.")
        if args.wait_seconds:
            print(f"Tenes {args.wait_seconds} segundos. Cuando termine, guardo la sesion y cierro.")
            await asyncio.sleep(args.wait_seconds)
        else:
            print("Cuando termines y veas tu cuenta logueada, volve a esta terminal y presiona Enter.")
            input()

        await context.close()
        print("Sesion guardada en el perfil persistente de Playwright.")


if __name__ == "__main__":
    asyncio.run(main())
