"""
AI Editorial Image Generator.
Generates high-resolution, watermark-free, text-free conceptual editorial illustrations
for LinkedIn Pulse covers, LinkedIn Feed posts, WhatsApp media notifications,
and Blog OpenGraph previews.

Supports:
1. Hugging Face Serverless Inference (FLUX.1-schnell) if HF_TOKEN is configured (100% free, studio grade, no watermarks).
2. Optimized Pollinations AI (FLUX / Turbo) as zero-config fallback with automated watermark cropping and contrast tuning.
"""

from io import BytesIO
import json
import logging
import os
from pathlib import Path
import time
from typing import Optional, Tuple
import urllib.parse
import urllib.request
from PIL import Image, ImageEnhance

from core.config import settings

logger = logging.getLogger(__name__)

# Strict negative constraints to prevent any text, letters, gibberish typography, or logos
NEGATIVE_CONSTRAINTS = (
    "strictly no text, no words, no letters, no typography, no captions, no titles, "
    "no watermarks, no logos, no signature, no labels, no symbols"
)


class EditorialImageGenerator:
    """
    Generates text-free, illustrative visual artworks for editorial content.
    """

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = Path(output_dir or settings.DATA_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.hf_token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")

    def build_clean_prompt(self, base_concept: str, category: Optional[str] = None) -> str:
        """
        Synthesizes an expressive, vivid editorial illustration prompt tailored to the theme
        without toxic hardcoded dark palettes, strictly banning text or watermarks.
        """
        cleaned = base_concept.strip()
        for flag in ["--ar", "--v", "--stylize", "--s", "--q"]:
            if flag in cleaned:
                cleaned = cleaned.split(flag)[0].strip()

        # Dynamic aesthetic styling based on theme keywords
        lower = cleaned.lower()
        cat_lower = (category or "").lower()

        if any(w in lower or w in cat_lower for w in ["música", "musica", "trilha", "cd", "vinil", "ferrovi", "trem"]):
            style_theme = (
                "modern conceptual editorial illustration, retro-futuristic artistic fusion, "
                "warm golden sunset lighting, rich amber and deep indigo harmonies, smooth gradient vectors, "
                "dynamic perspective, stylish album art aesthetic, studio quality"
            )
        elif any(w in lower or w in cat_lower for w in ["tribunal", "juiz", "direito", "advoc", "petiç", "process"]):
            style_theme = (
                "modern legal-tech editorial illustration, sleek isometric architectural composition, "
                "glowing golden scales of justice, transparent crystal elements, balanced ambient lighting, "
                "clean elegant corporate editorial art, high contrast"
            )
        elif any(w in lower or w in cat_lower for w in ["hack", "segurança", "ciber", "desalinhamento", "malware", "invas"]):
            style_theme = (
                "modern cybersecurity editorial illustration, sleek isometric digital vault, "
                "luminous fiber optic pathways, frosted acrylic surfaces, crisp laser accents, "
                "balanced cinematic tech lighting, sophisticated clean design"
            )
        else:
            style_theme = (
                "modern conceptual editorial tech illustration, clean isometric 3D vector aesthetic, "
                "vibrant balanced lighting, rich contrasting color palette, elegant minimalist composition, "
                "award-winning magazine cover art"
            )

        full_prompt = f"{style_theme}, {cleaned}, {NEGATIVE_CONSTRAINTS}"
        return full_prompt

    def _generate_via_huggingface(
        self,
        prompt: str,
        target_w: int,
        target_h: int,
    ) -> Optional[Image.Image]:
        """Generates via Hugging Face Serverless Inference API (FLUX.1-schnell)."""
        if not self.hf_token:
            return None

        model_url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
        headers = {
            "Authorization": f"Bearer {self.hf_token}",
            "Content-Type": "application/json",
            "User-Agent": "OmniFlow-Editorial/1.0",
        }
        payload = json.dumps({"inputs": prompt}).encode("utf-8")

        try:
            req = urllib.request.Request(model_url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as resp:
                if resp.status == 200:
                    raw_bytes = resp.read()
                    img = Image.open(BytesIO(raw_bytes))
                    logger.info("Successfully generated illustration via Hugging Face FLUX.1-schnell")
                    return img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        except Exception as e_hf:
            logger.warning("Hugging Face inference failed, falling back to Pollinations: %s", e_hf)

        return None

    def _generate_via_pollinations(
        self,
        prompt: str,
        target_w: int,
        target_h: int,
        seed: int,
    ) -> Optional[Image.Image]:
        """Generates via Pollinations AI with surgical bottom watermark removal."""
        req_w = target_w
        req_h = int(target_h * 1.08)  # Add 8% vertical buffer to safely crop out watermark
        encoded_prompt = urllib.parse.quote(prompt)

        url = (
            f"https://image.pollinations.ai/prompt/{encoded_prompt}"
            f"?width={req_w}&height={req_h}&model=flux&seed={seed}&nologo=true"
        )

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=55) as resp:
                if resp.status != 200:
                    logger.error("Pollinations returned HTTP %s", resp.status)
                    return None
                raw_bytes = resp.read()

            raw_img = Image.open(BytesIO(raw_bytes))
            curr_w, curr_h = raw_img.size

            # Surgically crop the bottom 7% to completely remove the watermark
            clean_img = raw_img.crop((0, 0, curr_w, int(curr_h * 0.93)))
            final_img = clean_img.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # Enhance contrast and saturation subtly for mobile thumbnail legibility
            final_img = ImageEnhance.Contrast(final_img).enhance(1.08)
            final_img = ImageEnhance.Color(final_img).enhance(1.05)

            return final_img
        except Exception as e_pol:
            logger.error("Pollinations generation failed: %s", e_pol)
            return None

    def generate_image(
        self,
        prompt: str,
        slug: str,
        format_type: str = "16:9",
        category: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> Optional[str]:
        """
        Generates a clean, watermark-free, text-free conceptual illustration.
        Saves locally and mirrors to public site directory.
        """
        clean_prompt = self.build_clean_prompt(prompt, category=category)
        actual_seed = seed if seed is not None else int(time.time())

        if format_type == "1:1":
            target_w, target_h = 1080, 1080
            prefix = "square-"
        else:
            target_w, target_h = 1200, 630
            prefix = "og-"

        filename = f"{prefix}{slug}.png"
        target_path = self.output_dir / filename

        logger.info(
            "Generating %s illustrative artwork for '%s' (target: %dx%d)...",
            format_type,
            slug,
            target_w,
            target_h,
        )

        final_img = None

        # 1. Try Hugging Face if token is configured
        if self.hf_token:
            final_img = self._generate_via_huggingface(clean_prompt, target_w, target_h)

        # 2. Fall back to optimized Pollinations engine
        if not final_img:
            final_img = self._generate_via_pollinations(clean_prompt, target_w, target_h, actual_seed)

        if not final_img:
            logger.error("Could not generate illustration for %s", slug)
            return None

        # Save image locally
        final_img.save(str(target_path), "PNG", optimize=True)
        logger.info("✅ Saved clean artwork to %s", target_path)

        # Mirror to public site directory
        public_locations = [
            Path("/root/site/public") / filename,
            Path("/app/site/public") / filename,
            Path(f"../site/public/{filename}"),
        ]
        for ploc in public_locations:
            try:
                if ploc.parent.exists():
                    final_img.save(str(ploc), "PNG", optimize=True)
                    logger.info("Mirrored artwork to public site at %s", ploc)
                    break
            except Exception:
                pass

        return str(target_path)


image_generator = EditorialImageGenerator()
