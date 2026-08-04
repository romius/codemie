# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from codemie.configs.logger import logger
from codemie.core.exceptions import ValidationException
from codemie_tools.data_management.file_system.image_generator import is_gemini_image_model
from codemie_tools.data_management.workspace.tools import BaseWorkspaceTool
from codemie_tools.data_management.workspace.tools_vars import GENERATE_WORKSPACE_IMAGE_TOOL_V2

_SIZE_PATTERN = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([xX:])\s*(\d+(?:[.,]\d+)?)\s*$")
_PNG_MIME_TYPE = "image/png"
_HTTP_TIMEOUT_SECONDS = 30
_BACKGROUND_VALUES = {"light", "dark"}
_MIN_DIMENSION = 1024
_MAX_DIMENSION = 1536
_BOUNDING_BOXES = {
    "landscape": (_MAX_DIMENSION, _MIN_DIMENSION),
    "square": (_MIN_DIMENSION, _MIN_DIMENSION),
    "portrait": (_MIN_DIMENSION, _MAX_DIMENSION),
}
_OUTPUT_FORMAT = "png"
_GEMINI_DEFAULT_IMAGE_SIZE = "1K"
_GEMINI_IMAGE_DIMENSIONS = {
    "1:1": {"512": (512, 512), "1K": (1024, 1024), "2K": (2048, 2048), "4K": (4096, 4096)},
    "1:4": {"512": (256, 1024), "1K": (512, 2048), "2K": (1024, 4096), "4K": (2048, 8192)},
    "1:8": {"512": (192, 1536), "1K": (384, 3072), "2K": (768, 6144), "4K": (1536, 12288)},
    "2:3": {"512": (424, 632), "1K": (848, 1264), "2K": (1696, 2528), "4K": (3392, 5056)},
    "3:2": {"512": (632, 424), "1K": (1264, 848), "2K": (2528, 1696), "4K": (5056, 3392)},
    "3:4": {"512": (448, 600), "1K": (896, 1200), "2K": (1792, 2400), "4K": (3584, 4800)},
    "4:1": {"512": (1024, 256), "1K": (2048, 512), "2K": (4096, 1024), "4K": (8192, 2048)},
    "4:3": {"512": (600, 448), "1K": (1200, 896), "2K": (2400, 1792), "4K": (4800, 3584)},
    "4:5": {"512": (464, 576), "1K": (928, 1152), "2K": (1856, 2304), "4K": (3712, 4608)},
    "5:4": {"512": (576, 464), "1K": (1152, 928), "2K": (2304, 1856), "4K": (4608, 3712)},
    "8:1": {"512": (1536, 192), "1K": (3072, 384), "2K": (6144, 768), "4K": (12288, 1536)},
    "9:16": {"512": (384, 688), "1K": (768, 1376), "2K": (1536, 2752), "4K": (3072, 5504)},
    "16:9": {"512": (688, 384), "1K": (1376, 768), "2K": (2752, 1536), "4K": (5504, 3072)},
    "21:9": {"512": (792, 168), "1K": (1584, 672), "2K": (3168, 1344), "4K": (6336, 2688)},
}
_SUPPORTED_GEMINI_ASPECT_RATIOS = tuple(_GEMINI_IMAGE_DIMENSIONS.keys())


@dataclass(frozen=True)
class ParsedSizeRequest:
    width: float
    height: float
    is_ratio: bool


@dataclass(frozen=True)
class ImageGenerationPlan:
    size: str | None
    output_format: str | None
    extra_body: dict[str, Any] | None
    canvas_width: int
    canvas_height: int
    target_width: int
    target_height: int
    preserve_generated_dimensions: bool


class GenerateWorkspaceImageToolV2Input(BaseModel):
    image_description: str = Field(
        description="Detailed image description or detailed user ask for generating an image."
    )
    size: str = Field(
        description="Requested image size in the format 'widthxheight', for example '1920x1080'. Or you can simple specify aspect ratio like '5:12'"
    )
    background: str = Field(
        default="light",
        description="Background hint for the generated image. Allowed values: 'light' or 'dark'.",
    )


class GenerateWorkspaceImageToolV2(BaseWorkspaceTool):
    name: str = GENERATE_WORKSPACE_IMAGE_TOOL_V2.name
    description: str = GENERATE_WORKSPACE_IMAGE_TOOL_V2.description or ""
    args_schema: Any = GenerateWorkspaceImageToolV2Input
    image_generator: Any | None = Field(default=None, exclude=True)

    def execute(self, image_description: str, size: str, background: str = "light") -> str:
        if not self.image_generator:
            raise ValidationException("Image generation is not configured.")

        size_request = parse_size_request(size)
        normalized_background = validate_background(background)
        plan = build_image_generation_plan(size=size_request, image_generator=self.image_generator)

        prompt = build_image_prompt(
            description=image_description,
            background=normalized_background,
            target_width=plan.target_width,
            target_height=plan.target_height,
        )

        try:
            output_bytes = generate_workspace_image_bytes(
                image_generator=self.image_generator,
                prompt=prompt,
                background=normalized_background,
                plan=plan,
            )
        except ValidationException as exc:
            image_generator_type = type(self.image_generator).__name__
            logger.info(f"Image generation validation error for image_generator={image_generator_type}: {exc}")
            raise ValidationException(f"{exc} (image_generator={image_generator_type})") from exc

        if plan.preserve_generated_dimensions:
            saved_width, saved_height = get_image_dimensions(output_bytes)
        else:
            saved_width, saved_height = plan.target_width, plan.target_height

        workspace_id = self._get_workspace_id()
        file_path = build_workspace_image_path()
        self.workspace_service.upsert_binary_file(workspace_id, file_path, output_bytes, self.user)
        sandbox_url = self.workspace_service.get_file_sandbox_url(workspace_id, file_path, self.user)

        return self._dump_json(
            {
                "workspace_path": file_path,
                "sandbox_url": sandbox_url,
                "mime_type": _PNG_MIME_TYPE,
                "width": saved_width,
                "height": saved_height,
            }
        )


def parse_size_request(size: str) -> ParsedSizeRequest:
    match = _SIZE_PATTERN.match(size or "")
    if not match:
        raise ValidationException(
            f"Invalid size format '{size}'. Expected format: 'widthxheight' (for example, '1920x1080')."
        )

    width = float(match.group(1).replace(",", "."))
    separator = match.group(2)
    height = float(match.group(3).replace(",", "."))
    if width <= 0 or height <= 0:
        raise ValidationException(f"Invalid dimensions: {width}x{height}. Both width and height must be positive.")
    return ParsedSizeRequest(width=width, height=height, is_ratio=separator == ":")


def parse_size_string(size: str) -> tuple[float, float]:
    parsed_size = parse_size_request(size)
    return parsed_size.width, parsed_size.height


def _get_image_generator_model_id(image_generator: Any) -> str | None:
    model_id = getattr(image_generator, "model_id", None)
    if isinstance(model_id, str) and model_id.strip():
        return model_id
    return None


def build_image_generation_plan(size: ParsedSizeRequest, image_generator: Any) -> ImageGenerationPlan:
    model_id = _get_image_generator_model_id(image_generator)
    if is_gemini_image_model(model_id):
        aspect_ratio = find_closest_gemini_aspect_ratio(size.width, size.height)
        image_size = select_gemini_image_size(size=size, aspect_ratio=aspect_ratio)
        output_width, output_height = _GEMINI_IMAGE_DIMENSIONS[aspect_ratio][image_size]
        return ImageGenerationPlan(
            size=None,
            output_format=None,
            extra_body={"response_format": {"image": {"aspect_ratio": aspect_ratio, "image_size": image_size}}},
            canvas_width=output_width,
            canvas_height=output_height,
            target_width=output_width,
            target_height=output_height,
            preserve_generated_dimensions=True,
        )

    canvas_width, canvas_height = get_canvas_dimensions(size.width, size.height)
    target_width, target_height = calculate_target_dimensions(size.width, size.height)
    return ImageGenerationPlan(
        size=f"{canvas_width}x{canvas_height}",
        output_format=_OUTPUT_FORMAT,
        extra_body=None,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        target_width=target_width,
        target_height=target_height,
        preserve_generated_dimensions=False,
    )


def find_closest_gemini_aspect_ratio(width: float, height: float) -> str:
    requested_ratio = width / height
    return min(
        _SUPPORTED_GEMINI_ASPECT_RATIOS,
        key=lambda aspect_ratio: abs(requested_ratio - parse_aspect_ratio(aspect_ratio)),
    )


def parse_aspect_ratio(aspect_ratio: str) -> float:
    left, right = aspect_ratio.split(":", 1)
    return int(left) / int(right)


def select_gemini_image_size(size: ParsedSizeRequest, aspect_ratio: str) -> str:
    if size.is_ratio:
        return _GEMINI_DEFAULT_IMAGE_SIZE

    return min(
        _GEMINI_IMAGE_DIMENSIONS[aspect_ratio],
        key=lambda image_size: get_gemini_dimension_distance(
            requested_width=size.width,
            requested_height=size.height,
            actual_dimensions=_GEMINI_IMAGE_DIMENSIONS[aspect_ratio][image_size],
        ),
    )


def get_gemini_dimension_distance(
    requested_width: float, requested_height: float, actual_dimensions: tuple[int, int]
) -> float:
    actual_width, actual_height = actual_dimensions
    return abs((actual_width / requested_width) - 1.0) + abs((actual_height / requested_height) - 1.0)


def validate_background(background: str) -> str:
    normalized_background = (background or "light").strip().lower()
    if normalized_background not in _BACKGROUND_VALUES:
        allowed = ", ".join(sorted(_BACKGROUND_VALUES))
        raise ValidationException(f"Invalid background '{background}'. Must be one of: {allowed}")
    return normalized_background


def classify_orientation(width: float, height: float) -> str:
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def calculate_target_dimensions(width: float, height: float) -> tuple[int, int]:
    max_width, max_height = get_canvas_dimensions(width, height)
    scale = min(max_width / width, max_height / height)
    scaled_width = int(round(width * scale))
    scaled_height = int(round(height * scale))
    return scaled_width, scaled_height


def get_canvas_dimensions(width: float, height: float) -> tuple[int, int]:
    return _BOUNDING_BOXES[classify_orientation(width, height)]


def get_orientation_hint(orientation: str, target_width: int, target_height: int) -> str:
    orientation_hint = ""

    if orientation == "landscape":
        percentage = target_height / _MIN_DIMENSION
        if percentage < 0.80:
            percentage -= 0.05  # to not have white/black bars
            orientation_hint = f"Landscape composition, draw in a full-width vertically centered horizontal strip that is {percentage * 100:.2f}% of total height"

    if orientation == "portrait":
        percentage = target_width / _MIN_DIMENSION
        if percentage < 0.80:
            percentage -= 0.05  # to not have white/black bars
            orientation_hint = f"Portrait composition, draw in a full-height horizontally centered vertical strip that is {percentage * 100:.2f}% of total width"

    return orientation_hint


def build_image_prompt(description: str, background: str, target_width: int, target_height: int) -> str:
    orientation = classify_orientation(target_width, target_height)
    orientation_hint = get_orientation_hint(orientation, target_width, target_height)
    return f"{description}\n\nAdditional constraints:\n- Composition {orientation}.\n{orientation_hint}"


def normalize_generated_image_bytes(url: str | None, b64_data: str | None) -> bytes:
    if b64_data:
        return base64.b64decode(b64_data)
    if not url:
        raise ValidationException("Image generation returned no image data.")
    if url.startswith("data:image"):
        return base64.b64decode(url.split(",", 1)[1])

    response = requests.get(url, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


def generate_workspace_image_bytes(
    image_generator: Any,
    prompt: str,
    background: str,
    plan: ImageGenerationPlan,
) -> bytes:
    base_image, mask_image = create_base_and_mask_images(
        canvas_width=plan.canvas_width,
        canvas_height=plan.canvas_height,
        target_width=plan.target_width,
        target_height=plan.target_height,
        background=background,
    )
    edit_kwargs = {
        "prompt": prompt,
        "image": base_image,
        "mask": mask_image,
    }
    if plan.size:
        edit_kwargs["size"] = plan.size
    if plan.output_format:
        edit_kwargs["output_format"] = plan.output_format
    if plan.extra_body:
        edit_kwargs["extra_body"] = plan.extra_body

    url, b64_data = image_generator.edit(**edit_kwargs)

    image_bytes = normalize_generated_image_bytes(url=url, b64_data=b64_data)
    if plan.preserve_generated_dimensions:
        return convert_image_to_png(image_bytes)
    return crop_image_to_dimensions(
        image_bytes,
        plan.canvas_width,
        plan.canvas_height,
        plan.target_width,
        plan.target_height,
    )


def resize_image_to_png(image_bytes: bytes, width: int, height: int) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as image:
        source_image = image.convert("RGBA") if image.mode in {"RGBA", "LA", "P"} else image.convert("RGB")
        resized = ImageOps.contain(source_image, (width, height), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        resized.save(output, format="PNG")
        return output.getvalue()


def convert_image_to_png(image_bytes: bytes) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as image:
        source_image = image.convert("RGBA") if image.mode in {"RGBA", "LA", "P"} else image.convert("RGB")
        output = io.BytesIO()
        source_image.save(output, format="PNG")
        return output.getvalue()


def get_image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(image_bytes)) as image:
        return image.size


def create_base_and_mask_images(
    canvas_width: int,
    canvas_height: int,
    target_width: int,
    target_height: int,
    background: str,
) -> tuple[bytes, bytes]:
    background_rgb = (255, 255, 255) if background == "light" else (0, 0, 0)
    base_image = Image.new("RGB", (canvas_width, canvas_height), background_rgb)
    mask_image = Image.new("RGBA", (canvas_width, canvas_height), background_rgb + (255,))

    left = (canvas_width - target_width) // 2
    top = (canvas_height - target_height) // 2
    right = left + target_width
    bottom = top + target_height

    transparent_mask = Image.new("RGBA", (target_width, target_height), background_rgb + (0,))
    mask_image.paste(transparent_mask, (left, top, right, bottom))

    base_output = io.BytesIO()
    base_image.save(base_output, format="PNG")

    mask_output = io.BytesIO()
    mask_image.save(mask_output, format="PNG")

    return base_output.getvalue(), mask_output.getvalue()


def crop_image_to_dimensions(
    image_bytes: bytes,
    canvas_width: int,
    canvas_height: int,
    target_width: int,
    target_height: int,
) -> bytes:
    with Image.open(io.BytesIO(image_bytes)) as image:
        source_image = image.convert("RGBA") if image.mode in {"RGBA", "LA", "P"} else image.convert("RGB")
        left = (canvas_width - target_width) // 2
        top = (canvas_height - target_height) // 2
        right = left + target_width
        bottom = top + target_height
        cropped = source_image.crop((left, top, right, bottom))

        output = io.BytesIO()
        cropped.save(output, format="PNG")
        return output.getvalue()


def build_workspace_image_path(now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"images/{timestamp}.png"
