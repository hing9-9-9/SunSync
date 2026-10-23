# Keep the draft Room table for existing databases; SceneRecord stores FR06 scenes.
from app.models.room import Room
from app.models.scene import SceneRecord
from app.ui_scene import UISpaceRecord

__all__ = ["Room", "SceneRecord", "UISpaceRecord"]
