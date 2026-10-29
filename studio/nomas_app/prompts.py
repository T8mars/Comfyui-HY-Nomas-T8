"""Local deterministic prompt form; no LLM or external prompt service."""
import copy
import json


def timestamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 60000:02d}:{(ms // 1000) % 60:02d}.{ms % 1000:03d}"


def add_turn(config, previous=None):
    duration = float(config.get("seconds", 2))
    frames = round(duration * 24)
    if not 4 <= frames <= 240 or frames % 4:
        raise ValueError("片段时长需对应 4 的整数倍帧，范围 1/6–10 秒。")
    if config.get("prompt_json", "").strip():
        document = json.loads(config["prompt_json"])
        if not isinstance(document, dict) or not document.get("segments"):
            raise ValueError("结构化提示词需要包含 segments 数组。")
        return document
    if previous:
        document = copy.deepcopy(previous)
        last = document["segments"][-1]["end_time"]
        minutes, sec = last.split(":")
        start = int(minutes) * 60 + float(sec)
    else:
        subject = config.get("subject", "a small orange fox").strip()
        document = {"Style": config.get("style", "Cinematic fantasy"),
                    "perspective_view": config.get("perspective", "third-person observer"),
                    "subject_list": [{"subject_id": "<|Subject1|>", "display_name": subject,
                                      "subject_attribute": config.get("appearance", subject)}], "segments": []}
        start = 0
    scene = config.get("scene", "").strip()
    change = config.get("change", "").strip() or "the visible scene, background, and other-object layout remain unchanged."
    if not change.startswith("Compared to the previous segment,"):
        change = "Compared to the previous segment, " + change
    action = config.get("action", "walks slowly forward").strip()
    if action and "<Action" not in action:
        action = f"<Action1><|Subject1|> {action}</Action1>."
    segment = {"start_time": timestamp(start), "end_time": timestamp(start + duration),
               "visual_description_long": scene if not document["segments"] else None,
               "description_change": change if document["segments"] else None,
               "contained_actions": [{"start_time": timestamp(start), "end_time": timestamp(start + duration),
                                      "action_description": action}] if action else []}
    document["segments"].append(segment)
    return document


def image_start_document(config):
    """Form mode uses a half-second still history, followed by the requested motion."""
    document = add_turn(config)
    if config.get("prompt_json", "").strip():
        return document
    document["segments"][0]["end_time"] = timestamp(.5)
    document["segments"][0]["contained_actions"] = []
    return add_turn(config, document)
