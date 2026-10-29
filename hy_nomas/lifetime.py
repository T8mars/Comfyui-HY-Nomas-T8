"""T8: release a native sampler's history on success, cancellation, and errors."""
import comfy.model_management


def sampling_cleanup(executor, *args, **kwargs):
    """ComfyUI OUTER_SAMPLE wrapper; no monkeypatch or external process is involved."""
    worlds = {}
    for conditions in getattr(executor.class_obj, "original_conds", {}).values():
        for condition in conditions or ():
            nomas = condition.get("nomas", {})
            world = nomas.get("world") if isinstance(nomas, dict) else None
            if world is not None:
                worlds[id(world)] = world
    try:
        # On 24 GiB cards the Qwen encoder must leave room for DiT activations/history.
        # The stock sampler reloads its required patchers through normal Comfy management.
        comfy.model_management.unload_all_models()
        return executor(*args, **kwargs)
    finally:
        for world in worlds.values():
            world.release_history()
