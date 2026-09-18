"""PTS-based video sampling and optional GPU decoding fallback."""
from i18n import tr
from concurrent.futures import CancelledError
from time import perf_counter
import av
from media import oriented_image
from analysis_common import check_cancel

def video_duration(container):
    if not container.streams.video:
        raise ValueError(tr('영상 스트림을 찾을 수 없습니다.'))
    stream = container.streams.video[0]
    return (float(stream.duration * stream.time_base) if stream.duration is not None
            else container.duration / av.time_base if container.duration is not None else None)


def clamp_analysis_end(path, start, end):
    with av.open(str(path)) as container:
        duration = video_duration(container)
    end = min(end, duration) if duration is not None else end
    if start >= end:
        raise ValueError(tr('영상 길이 안에서 구간을 선택해 주세요.'))
    return end


def samples(path, start, end, interval, cancel, *, decoder_threads=1, metrics=None, gpu=False,
            region=None, reference_size=None, on_frame=None):
    hardware = None
    if gpu:
        from av.codec.hwaccel import HWAccel
        hardware = HWAccel("d3d11va", allow_software_fallback=False)
    with av.open(str(path), **({"hwaccel": hardware} if hardware else {})) as container:
        if not container.streams.video:
            raise ValueError(tr('영상 스트림을 찾을 수 없습니다.'))
        stream = container.streams.video[0]
        stream.thread_type = "AUTO" if decoder_threads > 1 else "SLICE"
        stream.codec_context.thread_count = decoder_threads
        origin = stream.start_time or 0
        duration = video_duration(container)
        if duration is not None:
            end = min(end, duration)
        if start >= end:
            return
        container.seek(origin + int(start / stream.time_base), stream=stream, backward=True)
        target = start
        iterator = iter(container.decode(stream))
        while True:
            check_cancel(cancel)
            began = perf_counter()
            try:
                frame = next(iterator)
            except StopIteration:
                break
            if metrics is not None:
                metrics["decode_s"] += perf_counter() - began
                metrics["decoded_frames"] += 1
            check_cancel(cancel)
            if metrics is not None:
                metrics["decoder"] = "GPU · D3D11VA" if stream.codec_context.is_hwaccel else "CPU"
            if frame.pts is None:
                continue
            seconds = float((frame.pts - origin) * stream.time_base)
            if seconds >= end:
                break
            if on_frame is not None:
                on_frame(seconds, frame)
            if seconds + 1e-7 < target:
                continue
            began = perf_counter()
            image = oriented_image(frame, region=region, reference_size=reference_size)
            if metrics is not None:
                metrics["convert_s"] += perf_counter() - began
                metrics["sample_frames"] += 1
            yield seconds, image
            target = seconds + interval


def analysis_samples(path, start, end, interval, cancel, threads, metrics, gpu, report, warnings,
                     *, region=None, reference_size=None, on_frame=None):
    # Resume at the next sampling target if the experimental decoder fails mid-stream.
    resume = start
    if gpu:
        try:
            for seconds, image in samples(path, start, end, interval, cancel,
                                          decoder_threads=threads, metrics=metrics, gpu=True,
                                          region=region, reference_size=reference_size, on_frame=on_frame):
                resume = seconds + interval
                yield seconds, image
            return
        except CancelledError:
            raise
        except (av.FFmpegError, RuntimeError, ValueError, NotImplementedError) as error:
            message = tr('GPU 디코딩을 사용할 수 없어 CPU로 계속합니다: {error}', error=error)
            warnings.append(message)
            report(message)
    yield from samples(path, resume, end, interval, cancel, decoder_threads=threads, metrics=metrics,
                       region=region, reference_size=reference_size, on_frame=on_frame)
