from typing import Literal
from pydantic import BaseModel, Field


class GarmentAnalysis(BaseModel):
    name: str = Field(description="Concise descriptive name, e.g., 'Olive Quilted Overshirt'")
    category: Literal["top", "mid_layer", "outerwear", "bottom", "footwear", "accessory"]
    primary_color: str = Field(description="Dominant color name, e.g., 'navy', 'charcoal', 'white'")
    secondary_color: str = Field(description="Accent color, or 'none' if solid")
    warmth: int = Field(description="1 (hot summer) to 5 (deep winter coat)")
    formality: int = Field(description="1 (gym/lounge), 2 (casual), 3 (smart-casual), 4 (business), 5 (formal)")
    water_resistant: bool = Field(description="True if suitable for rain, slush, or snow")
    max_wears_before_wash: int = Field(
        description="Typical wears before laundry: 1 for base tees/polos/athletic, 3-4 for sweaters/chinos, 10+ for denim/jackets/shoes"
    )
    style_notes: str = Field(description="Short note on fabric texture, silhouette, and best pairings")


class WheringDetectedItem(BaseModel):
    box_2d: list[int] = Field(
        description="Tight 2D bounding box around ONLY the clothing item as [ymin, xmin, ymax, xmax] scaled 0 to 1000. Exclude grey eye/heart icons, headers, and the floating + button."
    )
    name: str = Field(description="Concise descriptive name, e.g., 'Navy Tipped Knit Polo'")
    category: Literal["top", "mid_layer", "outerwear", "bottom", "footwear", "accessory"]
    primary_color: str = Field(description="Dominant color name, e.g., 'navy', 'white', 'terracotta'")
    secondary_color: str = Field(description="Accent color, or 'none' if solid")
    warmth: int = Field(description="1 (hot summer) to 5 (deep winter coat)")
    formality: int = Field(description="1 (gym/lounge), 2 (casual), 3 (smart-casual), 4 (business), 5 (formal)")
    water_resistant: bool = Field(description="True if suitable for rain, slush, or snow")
    max_wears_before_wash: int = Field(
        description="Typical wears before laundry: 1 for base tees/polos/athletic, 3-4 for sweaters/chinos, 10+ for denim/jackets/shoes"
    )
    style_notes: str = Field(description="Short note on fabric texture, silhouette, and best pairings")


class WheringGridExtraction(BaseModel):
    items: list[WheringDetectedItem] = Field(
        description="All complete, unobstructed clothing items visible in the Whering grid screenshot"
    )


class OutfitOption(BaseModel):
    title: str = Field(description="Short vibe label, e.g., 'Smart-Casual Errands to Lunch'")
    item_ids: list[str] = Field(description="Exact garment IDs selected from the available clean wardrobe")
    rationale: str = Field(description="1-2 sentences tying this combo to today's hourly weather swing and schedule")


class DailyOutfitPlan(BaseModel):
    weather_takeaway: str = Field(description="One-line practical summary of how today's weather affects layering")
    outfits: list[OutfitOption] = Field(description="2 to 3 distinct outfit options ranked best-first")