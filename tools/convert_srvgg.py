#!/usr/bin/env python3
"""把 Real-ESRGAN 的 SRVGGNetCompact 权重（.pth）转换成 realesrgan-ncnn-vulkan 能用的 .param/.bin。

不依赖 PyTorch/numpy：直接读取 .pth（zip + pickle）里的张量，按 ncnn 格式写出
（卷积权重 fp16 + 4 字节标记，偏置和 PReLU 斜率为 fp32）。

用法：python3 tools/convert_srvgg.py realesr-general-x4v3.pth 输出目录/realesr-general-x4v3
"""

import pickle
import struct
import sys
import zipfile
from pathlib import Path

FP16_TAG = struct.pack("<I", 0x01306B47)


class _Tensor:
    def __init__(self, data: bytes, offset: int, size: tuple):
        n = 1
        for s in size:
            n *= s
        self.size = tuple(size)
        self.values = struct.unpack_from(f"<{n}f", data, offset * 4)


def load_state_dict(path: str) -> dict:
    zf = zipfile.ZipFile(path)
    prefix = zf.namelist()[0].split("/")[0]

    class Unpickler(pickle.Unpickler):
        def find_class(self, module, name):
            if module == "torch._utils" and name == "_rebuild_tensor_v2":
                return lambda storage, offset, size, stride, *a: _Tensor(storage, offset, size)
            if module == "torch" and name.endswith("Storage"):
                return name
            if module == "collections" and name == "OrderedDict":
                import collections
                return collections.OrderedDict
            raise pickle.UnpicklingError(f"不支持的对象 {module}.{name}")

        def persistent_load(self, pid):
            _, dtype, key, *_ = pid
            if dtype != "FloatStorage":
                raise ValueError(f"只支持 float32 权重，遇到 {dtype}")
            return zf.read(f"{prefix}/data/{key}")

    obj = Unpickler(zf.open(f"{prefix}/data.pkl")).load()
    for k in ("params_ema", "params"):
        if isinstance(obj, dict) and k in obj:
            return obj[k]
    return obj


def convert(pth: str, out_stem: str) -> None:
    sd = load_state_dict(pth)
    idx = sorted({int(k.split(".")[1]) for k in sd if k.startswith("body.")})
    convs = [i for i in idx if f"body.{i}.bias" in sd]
    prelus = [i for i in idx if f"body.{i}.bias" not in sd]
    last = sd[f"body.{convs[-1]}.weight"]
    upscale = int(round((last.size[0] / 3) ** 0.5))

    layers, blobs = [], ["data", "d0", "d1"]
    layers.append("Input            data     0 1 data")
    layers.append("Split            split    1 2 data d0 d1")
    binary = bytearray()
    cur = "d1"
    for i in idx:
        w = sd[f"body.{i}.weight"]
        out = f"b{i}"
        if i in convs:
            oc, ic, kh, kw = w.size
            layers.append(f"Convolution      conv{i}   1 1 {cur} {out} 0={oc} 1={kh} 4={kh // 2} 5=1 6={oc * ic * kh * kw}")
            binary += FP16_TAG + struct.pack(f"<{len(w.values)}e", *w.values)
            binary += struct.pack(f"<{oc}f", *sd[f"body.{i}.bias"].values)
        else:
            layers.append(f"PReLU            prelu{i}  1 1 {cur} {out} 0={w.size[0]}")
            binary += struct.pack(f"<{w.size[0]}f", *w.values)
        blobs.append(out)
        cur = out
    layers.append(f"PixelShuffle     ps       1 1 {cur} ps 0={upscale}")
    layers.append(f"Interp           up       1 1 d0 up 0=1 1={upscale:.6e} 2={upscale:.6e}")
    layers.append("BinaryOp         add      2 1 ps up output")
    blobs += ["ps", "up", "output"]

    Path(out_stem).parent.mkdir(parents=True, exist_ok=True)
    Path(out_stem + ".param").write_text(f"7767517\n{len(layers)} {len(blobs)}\n" + "\n".join(layers) + "\n")
    Path(out_stem + ".bin").write_bytes(bytes(binary))
    print(f"{out_stem}: {len(convs)} 层卷积, ×{upscale}, {len(binary)} 字节")


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
