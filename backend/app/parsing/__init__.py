"""论文解析层。

- base.py      解析结果的形状（chunk / element / link）与证据回查用的 SourceIndex
- anchors.py   送进模型的位置 anchor 约定，格式 [p1-2|<chunk_id>]
- benchmark.py 读 benchmark 数据目录（data/）的解析产物
- grobid.py    真实解析实现（TODO）
"""
