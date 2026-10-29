"""Bounded-memory MP4 writing and joining of locally generated clips."""
from pathlib import Path
from fractions import Fraction
import av
import numpy as np


def write_video(path, frames, fps=24):
    path = Path(path)
    temp = path.with_suffix(".partial.mp4")
    count = 0
    try:
        with av.open(str(temp), "w") as container:
            stream = container.add_stream("libx264", rate=fps)
            stream.width, stream.height = frames.shape[2], frames.shape[1]
            stream.pix_fmt = "yuv420p"
            stream.time_base = Fraction(1, fps)
            stream.options = {"crf": "18", "preset": "fast"}
            for frame in frames:
                array = frame.numpy() if hasattr(frame, "numpy") else frame
                if array.dtype != np.uint8:
                    array = (np.clip(array, 0, 1) * 255).round().astype(np.uint8)
                video_frame = av.VideoFrame.from_ndarray(array, format="rgb24")
                video_frame.pts, video_frame.time_base = count, Fraction(1, fps)
                for packet in stream.encode(video_frame):
                    container.mux(packet)
                count += 1
            for packet in stream.encode():
                container.mux(packet)
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return count


def join_clips(paths, output, fps=24):
    if not paths:
        raise ValueError("还没有可导出的视频。")
    output = Path(output)
    temp = output.with_suffix(".partial.mp4")
    count = 0
    try:
        with av.open(str(temp), "w") as destination:
            stream = None
            for path in paths:
                with av.open(str(path)) as source:
                    for frame in source.decode(video=0):
                        if stream is None:
                            stream = destination.add_stream("libx264", rate=fps)
                            stream.width, stream.height = frame.width, frame.height
                            stream.pix_fmt = "yuv420p"
                            stream.time_base = Fraction(1, fps)
                            stream.options = {"crf": "18", "preset": "fast"}
                        if (frame.width, frame.height) != (stream.width, stream.height):
                            raise ValueError("合并片段的分辨率必须一致。")
                        frame.pts, frame.time_base = count, Fraction(1, fps)
                        for packet in stream.encode(frame):
                            destination.mux(packet)
                        count += 1
            if stream is None:
                raise ValueError("片段中没有可解码的视频帧。")
            for packet in stream.encode():
                destination.mux(packet)
        temp.replace(output)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return count
