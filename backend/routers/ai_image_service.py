"""Unreleased legacy SEO image service.

The four routes and direct handlers fail closed. The worker returns without
processing retained tasks. Private generation helpers are not released entry points.
"""

import asyncio
import base64
import logging
from typing import Any, Dict, List, NoReturn, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel

from config import settings
from rate_limit import limit_ai

logger = logging.getLogger(__name__)


def _image_service_unavailable() -> NoReturn:
    raise HTTPException(status_code=404, detail="Not found")


class _UnreleasedImageRoute(APIRoute):
    def get_route_handler(self):
        # Refuse before FastAPI parses bodies or resolves auth/rate-limit dependencies.
        async def unavailable(request: Request):
            _image_service_unavailable()
        return unavailable


router = APIRouter(route_class=_UnreleasedImageRoute)

# ── In-memory task store ──────────────────────────────────────────────────────
_tasks: Dict[str, Dict[str, Any]] = {}
_worker_running = False

# ── Schemas ───────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    preset: str
    category: str
    product_name: str

class GenerateResponse(BaseModel):
    task_id: str

class TaskStatusResponse(BaseModel):
    status:         str                    # processing | done | error
    stage:          Optional[str] = None   # queued | generating | retrying | done | error
    progress:       Optional[int] = None   # 0-100
    completed_count: Optional[int] = None  # slides done so far
    slide_statuses: Optional[List[str]] = None  # per-slide: pending | done | failed
    image_urls:     Optional[List[str]] = None

class GenerateSingleRequest(BaseModel):
    preset: str
    category: str
    product_name: str
    slide_idx: int

class GenerateSingleResponse(BaseModel):
    background_url: str

class SuggestTextRequest(BaseModel):
    product_name: str
    category: str

class SuggestTextResponse(BaseModel):
    title_suggestions:   List[str]
    benefit_suggestions: List[str]
    cta_suggestions:     List[str]

# ── Preset library ────────────────────────────────────────────────────────────

_PRESET_STYLES: Dict[str, str] = {
    "premium":     "professional studio, soft box diffused lighting, white marble surface, light silver-gray background, premium minimalist product display, ultra-clean composition",
    "sale":        "bold vibrant background, vivid red and warm yellow accent colors, high contrast dramatic lighting, sale promotional energy, dynamic composition",
    "minimal":     "pure white clean background, single minimal shadow, flat lay composition, modern e-commerce zen style, uncluttered geometry",
    "tech":        "dark studio, deep navy background, electric blue neon accent glow, precision product photography, cinematic dramatic shadows",
    "beauty":      "soft blush pink and cream palette, natural diffused window light, blurred botanical flowers, luxury beauty editorial atmosphere",
    "marketplace": "neutral white studio, bright even lighting, standard marketplace e-commerce format, clean uncluttered background",
    "luxury":      "black velvet surface, warm gold rim highlight, dramatic deep shadows, exclusive prestige brand photography, ultra-luxury",
    "wb-style":    "bright white clean background, even studio lighting, Wildberries marketplace standard centered composition",
    "ozon-style":  "white background, soft commercial shadow, Ozon marketplace clean format, standard commercial photography",
}

_CATEGORY_CONTEXT: Dict[str, str] = {
    "auto":        "neutral versatile studio environment",
    "beauty":      "botanical backdrop, rose petals, marble surface, soft natural light",
    "home":        "cozy interior setting, warm wooden surface, home lifestyle context",
    "electronics": "dark precision studio, subtle circuit texture, technical environment",
    "clothes":     "clean fashion studio, lifestyle minimal background",
    "sport":       "dynamic outdoor or gym environment, energy and motion hints",
}

_SLIDE_MOODS = [
    "mood: confidence, premium quality, first impression",
    "mood: excitement, desire, emotional engagement",
    "mood: clarity, precision, technical authority",
    "mood: safety, trust, reliability, peace of mind",
    "mood: urgency, action, call-to-buy",
    "mood: satisfaction, brand loyalty, closing impression",
]


def _build_prompt(preset: str, category: str, product_name: str, slide_idx: int) -> str:
    style   = _PRESET_STYLES.get(preset, _PRESET_STYLES["minimal"])
    context = _CATEGORY_CONTEXT.get(category, _CATEGORY_CONTEXT["auto"])
    mood    = _SLIDE_MOODS[slide_idx % 6]
    return (
        f"Commercial product photography background only. {style}. {context}. {mood}. "
        f"Context: background image for {product_name}. "
        f"600x600 square. Photorealistic, commercial quality. "
        f"NO TEXT. NO WORDS. NO LABELS. NO PEOPLE. Pure background image."
    )

# ── Mock SVG backgrounds ──────────────────────────────────────────────────────

_MOCK_PALETTE = [
    ("#E8ECF0", "#C0C8D8"),  # Premium: silver → warm gray
    ("#5C0F0F", "#991A1A"),  # Sale: deep crimson → dark wine
    ("#FFFFFF", "#EBEBEB"),  # Minimal: near-white → light silver
    ("#040E28", "#0B225E"),  # Tech: dark navy → royal blue
    ("#2A0820", "#6B1F58"),  # Beauty: deep plum → dusty rose
    ("#0A0806", "#211608"),  # Luxury: near-black → warm espresso
]

def _mock_svg(idx: int) -> str:
    c_light, c_dark = _MOCK_PALETTE[idx % 6]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600">'
        f'<defs><radialGradient id="g" cx="38%" cy="32%" r="68%">'
        f'<stop offset="0%" stop-color="{c_light}"/>'
        f'<stop offset="100%" stop-color="{c_dark}"/>'
        f'</radialGradient></defs>'
        f'<rect width="600" height="600" fill="url(#g)"/>'
        f'</svg>'
    )
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"

_MOCK_BACKGROUNDS: List[str] = [_mock_svg(i) for i in range(6)]

# ── Parallel AI API ───────────────────────────────────────────────────────────

async def _call_slide(
    client: httpx.AsyncClient,
    api_key: str,
    prompt: str,
    idx: int,
    task_id: str,
) -> Optional[str]:
    """Single slide request, wrapped with per-slide timeout and status updates."""
    try:
        resp = await asyncio.wait_for(
            client.post(
                "https://api.nano-ai.io/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"prompt": prompt, "width": 600, "height": 600, "steps": 25},
            ),
            timeout=35.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            url = (
                data.get("url")
                or ((data.get("data") or [{}])[0]).get("url")
                or ((data.get("images") or [{}])[0]).get("url")
            )
            if url:
                logger.info("Task %s slide %d generated OK", task_id, idx)
                if task_id in _tasks:
                    _tasks[task_id]["slide_statuses"][idx] = "done"
                    _tasks[task_id]["completed_count"] += 1
                return url
        logger.warning("Task %s slide %d API status %s", task_id, idx, resp.status_code)
    except asyncio.TimeoutError:
        logger.warning("Task %s slide %d timed out", task_id, idx)
    except Exception as exc:
        logger.error("Task %s slide %d error: %s", task_id, idx, exc)

    if task_id in _tasks:
        _tasks[task_id]["slide_statuses"][idx] = "failed"
    return None


async def _call_ai_api_parallel(
    prompts: List[str],
    task_id: str,
) -> List[Optional[str]]:
    """
    Two-pass parallel generation:
      1st pass: all 6 slides in parallel.
      2nd pass: retry any None results (stage='retrying').
    Returns list of 6 items — None entries are replaced by mock SVGs by the caller.
    """
    api_key = getattr(settings, "nano_banana_api_key", "").strip()
    if not api_key:
        return [None] * 6

    if task_id in _tasks:
        _tasks[task_id]["stage"] = "generating"

    async with httpx.AsyncClient(timeout=60) as client:
        # First pass — all slides concurrently
        results: List[Optional[str]] = list(
            await asyncio.gather(
                *[
                    _call_slide(client, api_key, prompts[i], i, task_id)
                    for i in range(6)
                ],
                return_exceptions=False,
            )
        )

        # Second pass — retry failures
        failed_indices = [i for i, r in enumerate(results) if r is None]
        if failed_indices:
            if task_id in _tasks:
                _tasks[task_id]["stage"] = "retrying"
                for i in failed_indices:
                    _tasks[task_id]["slide_statuses"][i] = "pending"

            logger.info("Task %s retrying %d failed slides: %s", task_id, len(failed_indices), failed_indices)
            retry_results = await asyncio.gather(
                *[
                    _call_slide(client, api_key, prompts[i], i, task_id)
                    for i in failed_indices
                ],
                return_exceptions=False,
            )
            for idx, result in zip(failed_indices, retry_results):
                results[idx] = result

    return results


# ── Queue worker (asyncio background task) ────────────────────────────────────

async def queue_worker() -> None:
    """Main may still schedule this coroutine; no polling or retained-task processing."""
    return


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/ai/generate-background", response_model=GenerateResponse)
async def generate_background(body: GenerateRequest, request: Request, _rl: None = Depends(limit_ai)):
    _image_service_unavailable()


@router.get("/ai/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    _image_service_unavailable()


@router.post("/ai/generate-single", response_model=GenerateSingleResponse)
async def generate_single(body: GenerateSingleRequest, request: Request, _rl: None = Depends(limit_ai)):
    _image_service_unavailable()


@router.post("/ai/suggest-text", response_model=SuggestTextResponse)
async def suggest_text(body: SuggestTextRequest):
    _image_service_unavailable()
