from typing import Dict, Any, Optional


class UserProfileManager:

    def __init__(self):
        self.profile_cache: Dict[int, Dict[str, Any]] = {}

    def get_profile(self, user_id: int) -> Dict[str, Any]:
        if user_id not in self.profile_cache:
            self.profile_cache[user_id] = {
                "preferred_name": "主人",
                "likes": ["摸头", "小鱼干", "聊天"],
                "dislikes": ["踩尾巴", "大声吵闹"],
            }
        return self.profile_cache[user_id]

    def update_fact(self, user_id: int, key: str, value: Any) -> None:
        profile = self.get_profile(user_id)
        profile[key] = value

    def get_formatted_profile_prompt(self, user_id: int) -> str:
        profile = self.get_profile(user_id)
        return (f"[用户画像] 称呼: {profile.get('preferred_name', '主人')}, "
                f"喜好: {', '.join(profile.get('likes', []))}, "
                f"讨厌: {', '.join(profile.get('dislikes', []))}")
