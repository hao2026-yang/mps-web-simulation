# -*- coding: utf-8 -*-
"""MPS 排产仿真内核 V3（FJSP + 闭环 MRP II + 节点电价 + 分时碳价 + 虚拟电厂）

相对 V2 的升级
  1. 主数据：5 种物料（含外购件 M4、辅料 M5）、4 种半成品 P1—P4、3 种成品 FA—FC、
     6 台设备（新老代差：老设备慢但省电，新设备快但功率高），同一工序可有多台可选设备。
  2. 换型矩阵：按材质（钢/铝/外购/混合）取换产步数与成本，不再是固定值。
  3. 成本：新增人工标准工时与加班费、虚拟电厂调峰补偿、分时碳因子。
  4. 虚拟电厂：按随机种子触发 DR 事件（窗口 + 压降目标），储能满功率放电、柔性工序后移，
     按实际压降发放补贴；同时输出日前申报基线 vs 实际负荷。
  5. 报表：APP / MPS / MRP / CRP 四张闭环 MRP II 报表。

沿用 V2 的口径：1 步 = 15 min，96 步 = 24 h；碳排 = 购电量 × 分时碳因子。
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

T = 96
STEP_H = 0.25
DUE_STEP = 88
NIGHT_STEPS = set(range(0, 28)) | set(range(88, 96))
PEAK_LIMIT = 80.0
FREE_QUOTA = 120.0
DELAY_PENALTY = 20.0
SEMI = ("P1", "P2", "P3", "P4")
FINALS = ("FA", "FB", "FC")
COST_PER_CHANGE_STEP = 10.0


# ---------------------------------------------------------------- 主数据
@dataclass(frozen=True)
class Device:
    code: str
    name: str
    node: str
    speed: float          # 工时系数（>1 更慢）
    power: float          # 功率系数
    ops: Tuple[str, ...]  # 可执行工序
    old: bool


DEVICES: Dict[str, Device] = {
    "A1": Device("A1", "下料机（老）", "N1", 1.30, 0.85, ("下料", "锯切"), True),
    "A2": Device("A2", "加工中心（新）", "N1", 0.80, 1.20, ("下料", "锯切", "磨削", "切割", "铣削"), False),
    "B1": Device("B1", "机加中心（老）", "N1", 1.30, 0.85, ("磨削", "切割", "钻孔", "表面处理"), True),
    "B2": Device("B2", "机加中心（新）", "N1", 0.85, 1.15, ("磨削", "切割", "钻孔", "铣削", "表面处理"), False),
    "C1": Device("C1", "装配线", "N2", 1.00, 1.00, ("装配", "总装", "动平衡"), False),
    "C2": Device("C2", "测试与精加工线", "N2", 0.95, 1.10, ("测试", "抛光", "动平衡"), False),
}

NODES: Dict[str, str] = {"N1": "机加区节点", "N2": "装配区节点", "N3": "园区关口表"}


@dataclass(frozen=True)
class Material:
    code: str
    name: str
    kind: str            # raw / bought / aux
    unit_cost: float
    init_qty: int
    batch_qty: int
    lead_h: float


MATERIALS: Dict[str, Material] = {
    "M1": Material("M1", "钢材", "raw", 5.0, 30, 60, 6.0),
    "M2": Material("M2", "铝材", "raw", 6.0, 24, 50, 8.0),
    "M3": Material("M3", "标准件", "bought", 2.0, 50, 80, 12.0),
    "M4": Material("M4", "外购电机", "bought", 120.0, 8, 20, 18.0),
    "M5": Material("M5", "辅料（切削液等）", "aux", 1.0, 40, 100, 4.0),
}

MAT_GROUP = {"M1": "钢", "M2": "铝", "M3": "外购", "M4": "外购", "M5": "辅料"}

# 换型矩阵：前一材质 -> 后一材质 的换产步数
CHANGE_MATRIX: Dict[Tuple[str, str], int] = {
    ("钢", "钢"): 1, ("钢", "铝"): 3, ("钢", "外购"): 2, ("钢", "混合"): 3, ("钢", "辅料"): 1,
    ("铝", "钢"): 4, ("铝", "铝"): 1, ("铝", "外购"): 2, ("铝", "混合"): 3, ("铝", "辅料"): 1,
    ("外购", "钢"): 2, ("外购", "铝"): 2, ("外购", "外购"): 1, ("外购", "混合"): 2, ("外购", "辅料"): 1,
    ("混合", "钢"): 3, ("混合", "铝"): 3, ("混合", "外购"): 2, ("混合", "混合"): 2, ("混合", "辅料"): 1,
    ("辅料", "钢"): 1, ("辅料", "铝"): 1, ("辅料", "外购"): 1, ("辅料", "混合"): 1, ("辅料", "辅料"): 1,
}


@dataclass(frozen=True)
class Step:
    name: str
    devices: Tuple[str, ...]
    dur: int
    power: float


@dataclass(frozen=True)
class Product:
    code: str
    name: str
    kind: str
    group: str
    bom: Dict[str, int]
    route: Tuple[Step, ...]


PRODUCTS: Dict[str, Product] = {
    "P1": Product("P1", "P1 钢制半成品", "semi", "钢", {"M1": 2, "M5": 1},
                  (Step("下料", ("A1", "A2"), 1, 18), Step("磨削", ("B1", "B2"), 1, 12))),
    "P2": Product("P2", "P2 铝制半成品", "semi", "铝", {"M2": 2, "M3": 1},
                  (Step("切割", ("B1", "B2"), 1, 16), Step("钻孔", ("B1", "B2"), 1, 20))),
    "P3": Product("P3", "P3 精密半成品", "semi", "钢", {"M1": 1, "M3": 2},
                  (Step("锯切", ("A1", "A2"), 1, 14), Step("铣削", ("A2", "B2"), 1, 22))),
    "P4": Product("P4", "P4 转子组件", "semi", "外购", {"M4": 1, "M2": 1},
                  (Step("装配", ("C1",), 1, 20), Step("动平衡", ("C1", "C2"), 1, 18))),
    "FA": Product("FA", "FA 标准成品", "final", "混合", {"P1": 1, "P4": 1, "M4": 1},
                  (Step("总装", ("C1",), 2, 26), Step("测试", ("C2",), 1, 16))),
    "FB": Product("FB", "FB 铝制成品", "final", "铝", {"P2": 2, "M3": 2},
                  (Step("总装", ("C1",), 2, 26), Step("表面处理", ("B1", "B2"), 2, 22))),
    "FC": Product("FC", "FC 复合成品", "final", "混合", {"P1": 1, "P3": 1, "P4": 1, "M5": 2},
                  (Step("总装", ("C1",), 3, 30), Step("抛光", ("C2",), 2, 18))),
}


# ---------------------------------------------------------------- 参数
@dataclass
class UrgentOrder:
    product: str = "FA"
    qty: int = 2
    arrive_h: float = 12.0
    due_h: float = 18.0
    penalty: float = 60.0
    label: str = "急单"

    @property
    def arrive_step(self) -> int:
        return int(round(self.arrive_h * 4))

    @property
    def due_step(self) -> int:
        return int(round(self.due_h * 4))


@dataclass
class DRParams:
    enabled: bool = True
    declared_kw: float = 45.0   # 日前申报基线负荷
    target_kw: float = 8.0      # 要求压降目标
    subsidy: float = 6.0        # 元/(kW·h)
    avoid_ratio: float = 0.5    # 可中断工序比例（不是全部停机）
    events: Tuple[Tuple[int, int], ...] = ()   # 由 seed 生成


@dataclass
class Params:
    plans: Dict[str, int] = field(default_factory=lambda: {
        "P1": 10, "P2": 8, "P3": 6, "P4": 6, "FA": 4, "FB": 3, "FC": 2})
    batch_size: int = 2
    changeover_cost: float = 30.0
    workers: int = 3
    wage: float = 22.0
    overtime_rate: float = 1.5
    base_defect: float = 0.02
    night_defect_inc: float = 0.02
    rework_cost: float = 16.0
    fault_prob: float = 0.02
    ramp_limit: float = 25.0
    w_ele: float = 0.6
    use_pv: bool = True
    use_storage: bool = True
    use_flex: bool = True
    carbon_price: float = 60.0
    carbon_base: float = 0.65
    grid_mult: float = 1.0
    congestion: float = 1.0
    lead_scale: float = 1.0
    init_scale: float = 1.0
    urgent_orders: Tuple[UrgentOrder, ...] = ()
    dr: DRParams = field(default_factory=DRParams)
    seed: int = 42
    node_spread: Dict[str, float] = field(default_factory=lambda: {"N1": 0.06, "N2": 0.12, "N3": 0.0})
    node_loss: Dict[str, float] = field(default_factory=lambda: {"N1": 0.02, "N2": 0.03, "N3": 0.0})

    @property
    def w_carbon(self) -> float:
        return round(1.0 - self.w_ele, 2)


# ---------------------------------------------------------------- 价格 / 光伏 / 碳因子
def build_base_price(p: Params) -> np.ndarray:
    base = np.full(T, 0.5)
    base[0:28] = 0.3
    base[28:68] = 0.9
    base[68:96] = 0.5
    return base * p.grid_mult


def build_node_prices(p: Params) -> Dict[str, np.ndarray]:
    base = build_base_price(p)
    ratio = np.clip((base - 0.3 * p.grid_mult) / max(0.6 * p.grid_mult, 1e-6), 0.0, 1.0)
    return {n: base * (1.0 + p.node_loss[n]) + p.node_spread[n] * p.congestion * ratio
            for n in NODES}


def build_pv(p: Params, peak: float = 40.0) -> np.ndarray:
    pv = np.zeros(T)
    if p.use_pv:
        pv[28:68] = np.sin(np.linspace(0, np.pi, 40)) * peak
    return pv


def build_carbon_factor(p: Params, pv: np.ndarray) -> np.ndarray:
    """分时碳因子：夜间高、中午光伏大发时低。"""
    norm = pv / (pv.max() if pv.max() > 0 else 1.0)
    return p.carbon_base - 0.25 * norm


def build_dr_events(p: Params) -> Tuple[Tuple[int, int], ...]:
    if not p.dr.enabled:
        return ()
    if p.dr.events:
        return p.dr.events
    rng = random.Random(p.seed * 7 + 11)
    n = 1 if rng.random() < 0.5 else 2
    events = []
    for _ in range(n):
        start = rng.randint(28, 33)          # 早高峰（7:00-8:15）
        length = rng.choice([4, 5, 6])
        events.append((start, min(T - 2, start + length)))
    return tuple(sorted(events))


# ---------------------------------------------------------------- 任务
@dataclass
class Task:
    id: str
    product: str
    kind: str
    seq: int
    op: str
    devices: Tuple[str, ...]
    dur: int
    power: float
    prev: Optional[str]
    release: int = 0
    due: int = DUE_STEP
    urgent_id: int = -1
    qty: int = 1
    needs: Dict[str, int] = field(default_factory=dict)


def _chain(product: str, tag: str, qty: int, release: int, due: int, kind: str,
           urgent_id: int, tasks: Dict[str, Task]) -> None:
    prod = PRODUCTS[product]
    prev = None
    for i, step in enumerate(prod.route):
        tid = f"{tag}_{prod.code}_{i}"
        tasks[tid] = Task(tid, prod.code, kind, i, step.name, step.devices, step.dur,
                          step.power, prev, release, due, urgent_id, qty,
                          dict(prod.bom) if i == 0 else {})
        prev = tid


def build_tasks(p: Params) -> Dict[str, Task]:
    tasks: Dict[str, Task] = {}
    for code in SEMI:
        for i in range(int(p.plans.get(code, 0))):
            _chain(code, f"{code}_{i:02d}", 1, 0, DUE_STEP, "semi", -1, tasks)
    batch = max(1, int(p.batch_size))
    for code in FINALS:
        total = int(p.plans.get(code, 0))
        made, b = 0, 0
        while made < total:
            q = min(batch, total - made)
            _chain(code, f"{code}_b{b:02d}", q, 0, DUE_STEP, "batch", -1, tasks)
            made += q
            b += 1
    for u_idx, order in enumerate(p.urgent_orders):
        _chain(order.product, f"U{u_idx}{order.product}", int(order.qty),
               order.arrive_step, order.due_step, "urgent", u_idx, tasks)
    return tasks


def _hash01(text: str) -> float:
    return (zlib.crc32(text.encode("utf-8")) % 10_000) / 10_000.0


# ---------------------------------------------------------------- 结果
@dataclass
class Result:
    label: str
    total_cost: float
    ele_cost: float
    carbon: float
    carbon_cost: float
    mat_cost: float
    labor_cost: float
    overtime_h: float
    dr_subsidy: float
    flex_save: float
    penalties: Dict[str, float]
    schedule: List[dict]
    node_load: Dict[str, np.ndarray]
    total_load: np.ndarray
    grid_import: np.ndarray
    soc: np.ndarray
    inv: Dict[str, np.ndarray]
    mat_stock: Dict[str, np.ndarray]
    urgent_stats: List[dict]
    dr_stats: List[dict]
    dr_events: Tuple[Tuple[int, int], ...]
    baseline: np.ndarray
    device_util: Dict[str, dict]
    missing: int
    shortage_wait: int
    makespan: int
    night_share: float
    pv_selfuse: float
    mat_detail: Dict[str, float]
    prices: Dict[str, np.ndarray]
    base_price: np.ndarray
    carbon_factor: np.ndarray
    pv: np.ndarray
    reports: Dict[str, List[dict]]

    @property
    def delay_cost(self) -> float:
        return self.penalties["交期违约"]

    @property
    def on_time(self) -> bool:
        return all(s["on_time"] for s in self.urgent_stats) if self.urgent_stats else True

    def urgent_summary(self) -> str:
        if not self.urgent_stats:
            return "未启用急单"
        out = []
        for s in self.urgent_stats:
            st = "按期" if s["on_time"] else f"超期 {s['late_steps']} 步"
            out.append(f"{s['label']} 完成于 {s['finish'] * STEP_H:.2f} 时（{st}）")
        return "；".join(out)


class Simulator:
    def __init__(self, p: Params):
        self.p = p
        self.tasks = build_tasks(p)
        self.ids = list(self.tasks)
        self.prices = build_node_prices(p)
        self.base_price = build_base_price(p)
        self.pv = build_pv(p)
        self.carbon_factor = build_carbon_factor(p, self.pv)
        self.dr_events = build_dr_events(p)
        self.lead = {m: min(T - 2, max(1, int(round(MATERIALS[m].lead_h * 4 * p.lead_scale))))
                     for m in MATERIALS}
        self.init_qty = {m: max(1, int(round(MATERIALS[m].init_qty * p.init_scale))) for m in MATERIALS}
        self.receipts = {m: [(self.lead[m], MATERIALS[m].batch_qty),
                             (min(T - 2, self.lead[m] * 2), MATERIALS[m].batch_qty)]
                         for m in MATERIALS}

    # ---------- 物料 ----------
    def _stock(self, used, code, t):
        qty = self.init_qty[code]
        qty += sum(q for s, q in self.receipts[code] if s <= t)
        qty -= sum(q for s, q in used[code] if s <= t)
        return qty

    def _material_start(self, used, needs, t0):
        t = t0
        shifted = False
        for code, need in needs.items():
            guard = 0
            while self._stock(used, code, t) < need:
                arrivals = [s for s, _ in self.receipts[code] if s > t]
                if not arrivals:
                    return T + 2, True
                t = min(arrivals)
                shifted = True
                guard += 1
                if guard > 3:
                    return T + 2, True
        return t, shifted

    # ---------- 半成品齐套 ----------
    @staticmethod
    def _semi_ready(prod_ev, cons_ev, code, need, t0):
        def stock(t):
            return (sum(q for e, q in prod_ev[code] if e <= t)
                    - sum(q for e, q in cons_ev[code] if e <= t))
        if stock(t0) >= need:
            return t0
        for end, _ in sorted(prod_ev[code]):
            if end > t0 and stock(end) >= need:
                return end
        return None

    def _in_dr(self, t0: int, t1: int) -> bool:
        return any(not (t1 <= a or t0 >= b) for a, b in self.dr_events)

    # ---------- 主流程 ----------
    def run(self, order: Sequence[str], label: str = "") -> Result:
        p = self.p
        uniq = [t for t in dict.fromkeys(order) if t in self.tasks]
        pending = uniq + [t for t in self.ids if t not in set(uniq)]

        dev_free = {d: 0 for d in DEVICES}
        dev_last = {d: None for d in DEVICES}
        dev_last_p = {d: 0.0 for d in DEVICES}
        used = {m: [] for m in MATERIALS}
        prod_ev = {s: [] for s in SEMI}
        cons_ev = {s: [] for s in SEMI}

        node_load = {n: np.zeros(T) for n in NODES}
        sche: List[dict] = []
        placed: Dict[str, dict] = {}
        pen_change = pen_delay = pen_rework = pen_short = 0.0
        flex_save = 0.0
        wait_total = 0
        missing = 0

        while pending:
            progressed = False
            for tid in list(pending):
                task = self.tasks[tid]
                if task.prev and task.prev not in placed:
                    continue

                dev = min(task.devices, key=lambda d: (dev_free[d], DEVICES[d].old))
                start = max(dev_free[dev], task.release)
                if task.prev:
                    start = max(start, placed[task.prev]["End"])

                semi_need = {k: v * task.qty for k, v in task.needs.items() if k in SEMI}
                if semi_need:
                    ready = start
                    ok = True
                    for code, need in semi_need.items():
                        r = self._semi_ready(prod_ev, cons_ev, code, need, ready)
                        if r is None:
                            ok = False
                            break
                        ready = max(ready, r)
                    if not ok:
                        continue
                    start = ready

                # 换型：按材质矩阵
                prev_group = dev_last[dev]
                if prev_group is not None and prev_group != PRODUCTS[task.product].group:
                    steps = CHANGE_MATRIX.get((prev_group, PRODUCTS[task.product].group), 2)
                    start += steps
                    pen_change += steps * COST_PER_CHANGE_STEP
                elif prev_group is not None:
                    steps = CHANGE_MATRIX.get((prev_group, prev_group), 1)
                    if steps > 1:
                        start += steps
                        pen_change += steps * COST_PER_CHANGE_STEP

                speed = DEVICES[dev].speed
                dur = max(1, int(round(task.dur * speed)))
                power = task.power * DEVICES[dev].power

                last_p = dev_last_p[dev] if dev_last[dev] is not None else 0.0
                actual_p = min(power, last_p + p.ramp_limit) if last_p else power

                if p.fault_prob > 0 and _hash01(f"{tid}|{p.seed}") < p.fault_prob:
                    start += 2

                flexible = task.kind == "semi" or (task.kind == "batch")
                # 虚拟电厂：柔性工序避开 DR 窗口
                if (p.dr.enabled and flexible and start < T - 1
                        and _hash01(f"{tid}|dr") < p.dr.avoid_ratio):
                    for a, b in self.dr_events:
                        if a <= start < b:
                            start = b
                            break

                # 柔性：光伏富余 / 低价时段
                if p.use_flex and flexible and start < T - 1:
                    node_code = DEVICES[dev].node

                    def margin(step: int) -> float:
                        load_now = sum(float(node_load[n][step]) for n in NODES) + actual_p
                        cover = float(self.pv[step]) / max(load_now, 1e-6)
                        return float(self.prices[node_code][step]) * max(0.0, 1.0 - cover)

                    best, best_s = start, margin(start)
                    for s in range(start, min(start + 16, T)):
                        sc = margin(s)
                        if sc < best_s - 0.02:
                            best, best_s = s, sc
                    if best > start:
                        saved = (float(self.prices[node_code][start])
                                 - float(self.prices[node_code][best])) * actual_p * STEP_H * dur
                        flex_save += max(0.0, saved)
                        start = best

                mat_needs = {k: v * task.qty for k, v in task.needs.items() if k in MATERIALS}
                wait = 0
                if mat_needs:
                    m_start, shifted = self._material_start(used, mat_needs, start)
                    if shifted:
                        wait = max(0, m_start - start)
                        start = m_start
                        wait_total += wait
                    if start > T:
                        pen_short += 120.0
                        missing += 1
                        start = T - 1
                    for code, need in mat_needs.items():
                        used[code].append((start, need))

                end = min(start + dur, T)
                if end > task.due and task.kind != "urgent":
                    pen_delay += (end - task.due) * DELAY_PENALTY

                node = DEVICES[dev].node
                for s in range(start, end):
                    node_load[node][s] += actual_p
                    defect = p.base_defect + (p.night_defect_inc if s in NIGHT_STEPS else 0.0)
                    pen_rework += p.rework_cost * defect

                for code, need in semi_need.items():
                    cons_ev[code].append((start, need))
                if task.product in SEMI and task.seq == len(PRODUCTS[task.product].route) - 1:
                    prod_ev[task.product].append((end, task.qty))

                placed[tid] = {"Start": start, "End": end}
                dev_free[dev] = end
                dev_last[dev] = PRODUCTS[task.product].group
                dev_last_p[dev] = actual_p
                sche.append({"Task": tid, "Product": task.product, "Device": dev,
                             "Node": node, "Start": start, "End": end, "Duration": end - start,
                             "Power": actual_p, "Qty": task.qty, "Kind": task.kind,
                             "Urgent": task.kind == "urgent", "Wait": wait, "Op": task.op})
                pending.remove(tid)
                progressed = True

            if not progressed:
                tid = pending.pop(0)
                task = self.tasks[tid]
                dev = task.devices[0]
                start = max(dev_free[dev], task.release, T - 6)
                end = min(start + task.dur, T)
                pen_short += 120.0
                missing += 1
                node_load[DEVICES[dev].node][start:end] += task.power
                placed[tid] = {"Start": start, "End": end}
                dev_free[dev] = end
                dev_last[dev] = PRODUCTS[task.product].group
                sche.append({"Task": tid, "Product": task.product, "Device": dev,
                             "Node": DEVICES[dev].node, "Start": start, "End": end,
                             "Duration": end - start, "Power": task.power, "Qty": task.qty,
                             "Kind": task.kind, "Urgent": task.kind == "urgent",
                             "Wait": 0, "Op": task.op})

        return self._settle(label, sche, node_load, used, prod_ev, cons_ev,
                            pen_delay, pen_change, pen_rework, pen_short, flex_save,
                            wait_total, missing)

    # ---------- 结算 ----------
    def _settle(self, label, sche, node_load, used, prod_ev, cons_ev,
                pen_delay, pen_change, pen_rework, pen_short, flex_save,
                wait_total, missing) -> Result:
        p = self.p
        total_load = sum(node_load[n] for n in NODES)
        soc_curve = np.zeros(T)
        grid_import = np.zeros(T)
        ele_cost = 0.0
        soc = 0.5
        served_pv = 0.0
        in_dr = np.zeros(T, dtype=bool)
        for a, b in self.dr_events:
            in_dr[a:b] = True

        for t in range(T):
            load = float(total_load[t])
            if p.use_storage:
                if in_dr[t] and soc > 0.1:
                    dis = min(20.0, (soc - 0.1) * 100 / STEP_H)
                    soc = max(0.1, soc - dis * STEP_H / 100)
                    load = max(0.0, load - dis)
                elif self.base_price[t] < 0.4 and soc < 0.9:
                    ch = min(30.0, (0.9 - soc) * 100 / STEP_H)
                    soc = min(0.9, soc + ch * STEP_H / 100)
                    load += ch
                elif self.base_price[t] > 0.85 and soc > 0.1:
                    dis = min(30.0, (soc - 0.1) * 100 / STEP_H)
                    soc = max(0.1, soc - dis * STEP_H / 100)
                    load = max(0.0, load - dis)
            soc_curve[t] = soc

            served = min(self.pv[t], load)
            served_pv += served
            net = max(0.0, load - self.pv[t])
            grid_import[t] = net

            if total_load[t] > 0:
                shares = {n: float(node_load[n][t]) / float(total_load[t]) for n in NODES}
            else:
                shares = {n: 1.0 / len(NODES) for n in NODES}
            for n in NODES:
                ele_cost += float(self.prices[n][t]) * net * shares[n] * STEP_H

        # 分时碳因子
        carbon = float((grid_import * self.carbon_factor).sum() * STEP_H)
        carbon_cost = ((carbon - FREE_QUOTA) * p.carbon_price if carbon > FREE_QUOTA
                       else -(FREE_QUOTA - carbon) * 30.0)
        pen_peak = float(sum(max(0.0, v - PEAK_LIMIT) * 5.0 for v in total_load))

        # 人工：标准工时 + 加班
        labor_h = sum(e["Duration"] * STEP_H for e in sche)
        regular_h = 8.0 * max(1, p.workers)
        overtime_h = max(0.0, labor_h - regular_h)
        labor_cost = labor_h * p.wage + overtime_h * p.wage * (p.overtime_rate - 1.0)

        # 虚拟电厂：日前申报基线（当日平均负荷）与实际执行
        baseline = np.full(T, np.nan)
        for a, b in self.dr_events:
            baseline[a:b] = p.dr.declared_kw
        dr_stats, dr_subsidy = [], 0.0
        for a, b in self.dr_events:
            seg = slice(a, b)
            base = p.dr.declared_kw
            act = float(total_load[seg].mean())
            cut = max(0.0, base - act)
            ok = cut >= p.dr.target_kw * 0.9
            sub = p.dr.subsidy * min(cut, p.dr.target_kw) * (b - a) * STEP_H if ok else \
                p.dr.subsidy * cut * (b - a) * STEP_H * 0.5
            dr_subsidy += sub
            dr_stats.append({"window": f"{a * 15 // 60:02d}:{a * 15 % 60:02d}-{b * 15 // 60:02d}:{b * 15 % 60:02d}",
                             "start": a, "end": b, "target": p.dr.target_kw,
                             "baseline": round(base, 2), "actual": round(act, 2),
                             "cut": round(cut, 2), "pass": bool(ok), "subsidy": round(sub, 2)})

        mat_detail = {m: sum(q for _, q in used[m]) * MATERIALS[m].unit_cost for m in MATERIALS}
        mat_cost = sum(mat_detail.values())

        urgent_stats = []
        for u_idx, order in enumerate(p.urgent_orders):
            ts = [e for e in sche if e["Task"].startswith(f"U{u_idx}{order.product}_")]
            if not ts:
                continue
            finish = max(e["End"] for e in ts)
            late = max(0, finish - order.due_step)
            urgent_stats.append({"label": order.label or f"{order.product}×{order.qty}",
                                 "product": order.product, "qty": order.qty,
                                 "arrive": order.arrive_step, "due": order.due_step,
                                 "finish": finish, "late_steps": late, "on_time": late == 0,
                                 "penalty": late * order.penalty})
        pen_urgent = sum(s["penalty"] for s in urgent_stats)

        total_cost = (p.w_ele * ele_cost + p.w_carbon * carbon_cost + mat_cost + labor_cost
                      + pen_delay + pen_change + pen_rework + pen_peak + pen_short
                      + pen_urgent - dr_subsidy)

        inv = {}
        for s in SEMI:
            curve = np.zeros(T)
            for t in range(T):
                curve[t] = max(0.0, (sum(q for e, q in prod_ev[s] if e <= t)
                                     - sum(q for e, q in cons_ev[s] if e <= t)))
            inv[s] = curve
        mat_stock = {m: np.array([max(0, self._stock(used, m, t)) for t in range(T)], dtype=float)
                     for m in MATERIALS}

        makespan = max((e["End"] for e in sche), default=0)
        night_energy = sum(e["Power"] * e["Duration"] for e in sche
                           if any(t in NIGHT_STEPS for t in range(e["Start"], e["End"])))
        all_energy = sum(e["Power"] * e["Duration"] for e in sche) or 1.0
        pv_energy = float(self.pv.sum() * STEP_H)

        # 设备利用率
        dev_util = {}
        for d in DEVICES:
            busy = sum(e["Duration"] for e in sche if e["Device"] == d)
            dev_util[d] = {"busy": busy, "rate": busy / T, "jobs": sum(1 for e in sche if e["Device"] == d)}

        reports = build_reports(p, sche, used, dev_util, urgent_stats)

        return Result(
            label=label, total_cost=total_cost, ele_cost=ele_cost, carbon=carbon,
            carbon_cost=carbon_cost, mat_cost=mat_cost, labor_cost=labor_cost,
            overtime_h=overtime_h, dr_subsidy=dr_subsidy, flex_save=flex_save,
            penalties={
                "交期违约": round(pen_delay + pen_urgent, 2),
                "换产成本": round(pen_change, 2),
                "返工成本": round(pen_rework, 2),
                "需量超容": round(pen_peak, 2),
                "缺料惩罚": round(pen_short, 2),
                "碳交易(负为补贴)": round(carbon_cost, 2),
                "物料成本": round(mat_cost, 2),
                "人工与加班": round(labor_cost, 2),
                "VPP调峰补贴": round(-dr_subsidy, 2),
                "柔性节约收益": round(flex_save, 2),
            },
            schedule=sche, node_load=node_load, total_load=total_load,
            grid_import=grid_import, soc=soc_curve, inv=inv, mat_stock=mat_stock,
            urgent_stats=urgent_stats, dr_stats=dr_stats, dr_events=self.dr_events,
            baseline=baseline,
            device_util=dev_util, missing=missing, shortage_wait=wait_total,
            makespan=makespan, night_share=night_energy / all_energy,
            pv_selfuse=served_pv / pv_energy if pv_energy > 0 else 0.0,
            mat_detail={k: round(v, 2) for k, v in mat_detail.items()},
            prices=self.prices, base_price=self.base_price,
            carbon_factor=self.carbon_factor, pv=self.pv, reports=reports,
        )


# ---------------------------------------------------------------- MRP II 报表
def build_reports(p: Params, sche, used, dev_util, urgent_stats) -> Dict[str, List[dict]]:
    produced: Dict[str, float] = {}
    for e in sche:
        prod = PRODUCTS[e["Product"]]
        if e["Task"].endswith(f"_{len(prod.route) - 1}"):
            produced[e["Product"]] = produced.get(e["Product"], 0) + e["Qty"]

    # APP：产销平衡
    app = []
    totals = {"产出": 0, "需求": 0}
    for code in FINALS:
        plan = int(p.plans.get(code, 0))
        out = produced.get(code, 0)
        app.append({"产品": code, "需求/订单": plan, "计划产出": out,
                    "期末库存": max(0, out - plan), "达成率": f"{(out / plan * 100 if plan else 0):.0f}%"})
        totals["产出"] += out
        totals["需求"] += plan
    app.append({"产品": "合计", "需求/订单": totals["需求"], "计划产出": totals["产出"],
                "期末库存": max(0, totals["产出"] - totals["需求"]),
                "达成率": f"{(totals['产出'] / totals['需求'] * 100 if totals['需求'] else 0):.0f}%"})

    # MPS：成品批次计划
    mps = []
    for code in FINALS:
        batches = sorted({e["Task"].split("_")[1] for e in sche
                          if e["Product"] == code and "_b" in e["Task"]})
        done = produced.get(code, 0)
        mps.append({"产品": code, "订单量": int(p.plans.get(code, 0)),
                    "批量": max(1, int(p.batch_size)), "批次数": len(batches),
                    "计划产出": int(done), "期末库存": max(0, int(done - p.plans.get(code, 0))),
                    "交付状态": "满足" if done >= p.plans.get(code, 0) else "缺量"})

    # MRP：毛需求 → 净需求 → 采购
    gross: Dict[str, float] = {m: 0.0 for m in MATERIALS}
    for code, qty in produced.items():
        for m, per in PRODUCTS[code].bom.items():
            if m in gross:
                gross[m] += per * qty
    mrp = []
    for m in MATERIALS:
        init = MATERIALS[m].init_qty
        recv = MATERIALS[m].batch_qty
        need = gross[m]
        net = max(0.0, need - init - recv)
        first_need = min([e["Start"] for e in sche
                          if e["Task"].endswith("_0") and m in PRODUCTS[e["Product"]].bom] or [T])
        order_at = max(0, int(first_need - MATERIALS[m].lead_h * 4))
        mrp.append({"物料": m, "名称": MATERIALS[m].name, "毛需求": int(round(need)),
                    "期初库存": init, "计划到货": int(round(recv)),
                    "净需求": int(round(net)),
                    "采购下达": f"{order_at * 15 // 60:02d}:{order_at * 15 % 60:02d}" if net > 0 else "—",
                    "提前期(h)": MATERIALS[m].lead_h})

    # CRP：设备产能负荷
    crp = []
    for d, info in dev_util.items():
        cap = T
        load = info["busy"]
        rate = load / cap * 100
        crp.append({"设备": d, "名称": DEVICES[d].name, "可用产能/步": cap,
                    "实际负荷/步": load, "负荷率": f"{rate:.1f}%",
                    "状态": "超载" if rate > 95 else ("紧张" if rate > 80 else "正常")})

    return {"APP": app, "MPS": mps, "MRP": mrp, "CRP": crp}


# ---------------------------------------------------------------- 算法
def greedy_order(tasks: Dict[str, Task]) -> List[str]:
    def key(tid):
        t = tasks[tid]
        rank = 0 if t.kind == "urgent" else (1 if t.product in FINALS else 2)
        return (rank, t.due, t.release, tid)
    return sorted(tasks, key=key)


def _ox(a, b, rng):
    n = len(a)
    i, j = sorted(rng.sample(range(n), 2))
    child: List[Optional[str]] = [None] * n
    child[i:j] = a[i:j]
    fill = [x for x in b if x not in child]
    k = 0
    for idx in range(n):
        if child[idx] is None:
            child[idx] = fill[k]
            k += 1
    return child


def run_greedy(p: Params, label: str = "方案A 敏捷派") -> Result:
    sim = Simulator(p)
    return sim.run(greedy_order(sim.tasks), label=label)


def _seeds(tasks):
    ids = list(tasks)
    by_product = []
    for code in list(SEMI) + list(FINALS):
        by_product += [t for t in ids if tasks[t].product == code]
    return [greedy_order(tasks), by_product,
            sorted(ids, key=lambda t: (tasks[t].product, tasks[t].seq))]


def _local(sim, order, passes=1):
    best = list(order)
    best_c = sim.run(best).total_cost
    for _ in range(passes):
        moved = False
        for i in range(len(best) - 1):
            cand = best[:]
            cand[i], cand[i + 1] = cand[i + 1], cand[i]
            c = sim.run(cand).total_cost
            if c < best_c - 1e-9:
                best, best_c, moved = cand, c, True
        if not moved:
            break
    return best_c, best


def run_ga(p: Params, pop_size: int = 20, generations: int = 16,
           label: str = "方案B 统筹派", seed: Optional[int] = None, local_search: bool = True) -> Result:
    sim = Simulator(p)
    ids = sim.ids
    if len(ids) < 2:
        return sim.run(ids, label=label)
    rng = random.Random(p.seed if seed is None else seed)
    pop = [rng.sample(ids, len(ids)) for _ in range(pop_size)]
    for i, order in enumerate(_seeds(sim.tasks)[:pop_size]):
        pop[i] = list(order)
    best_res, best_cost = None, math.inf
    for gen in range(generations):
        scored = []
        for ind in pop:
            r = sim.run(ind, label=label)
            scored.append((r.total_cost, ind, r))
            if r.total_cost < best_cost:
                best_cost, best_res = r.total_cost, r
        scored.sort(key=lambda x: x[0])
        elite = [x[1] for x in scored[: max(2, pop_size // 5)]]
        pop = elite[:]
        while len(pop) < pop_size:
            a, b = rng.choice(elite), rng.choice(elite)
            child = _ox(a, b, rng)
            if rng.random() < 0.3:
                i, j = rng.sample(range(len(child)), 2)
                child[i], child[j] = child[j], child[i]
            pop.append(child)
        if local_search and gen % 4 == 3:
            c, order = _local(sim, elite[0], 1)
            if c < best_cost:
                best_cost, best_res = c, sim.run(order, label=label)
                pop[0] = order
    assert best_res is not None
    best_res.label = label
    return best_res


# ---------------------------------------------------------------- 场景
def with_urgent(p: Params, order: Optional[UrgentOrder]) -> Params:
    return replace(p, urgent_orders=(order,) if order else ())


def urgent_presets() -> Dict[str, Optional[UrgentOrder]]:
    return {
        "不插急单": None,
        "① FA×2｜12:00 到｜18:00 交": UrgentOrder("FA", 2, 12.0, 18.0, 60, "急单①FA×2"),
        "② FB×3｜09:00 到｜15:00 交": UrgentOrder("FB", 3, 9.0, 15.0, 60, "急单②FB×3"),
        "③ FC×1｜14:00 到｜20:00 交": UrgentOrder("FC", 1, 14.0, 20.0, 60, "急单③FC×1"),
        "④ FC×2｜10:00 到｜14:00 交（最紧）": UrgentOrder("FC", 2, 10.0, 14.0, 90, "急单④FC×2"),
    }


def control_cases() -> List[Tuple[str, dict]]:
    return [
        ("场景1 基准", dict(use_pv=False, use_storage=False, use_flex=False)),
        ("场景2 仅光伏", dict(use_pv=True, use_storage=False, use_flex=False)),
        ("场景3 光储", dict(use_pv=True, use_storage=True, use_flex=False)),
        ("场景4 光储+柔性", dict(use_pv=True, use_storage=True, use_flex=True)),
    ]


def bom_table() -> List[dict]:
    return [{"产品": c, "名称": p.name, "类型": "半成品" if p.kind == "semi" else "成品",
             "材质": p.group,
             "BOM 构成": " + ".join(f"{k}×{v}" for k, v in p.bom.items()),
             "工艺路线": " → ".join(f"{s.name}({'/'.join(s.devices)},{s.dur}步,{s.power:.0f}kW)"
                                   for s in p.route)}
            for c, p in PRODUCTS.items()]


def material_table() -> List[dict]:
    kind = {"raw": "原材料", "bought": "外购件", "aux": "辅料"}
    return [{"物料": m.code, "名称": m.name, "类别": kind[m.kind], "单价": m.unit_cost,
             "期初库存": m.init_qty, "到货批量": m.batch_qty, "提前期(h)": m.lead_h}
            for m in MATERIALS.values()]


def device_table() -> List[dict]:
    return [{"设备": d.code, "名称": d.name, "节点": d.node,
             "工时系数": d.speed, "功率系数": d.power,
             "可执行工序": "、".join(d.ops), "新旧": "老" if d.old else "新"}
            for d in DEVICES.values()]


def change_matrix_rows() -> List[dict]:
    groups = ["钢", "铝", "外购", "混合", "辅料"]
    rows = []
    for g in groups:
        row = {"从\\到": g}
        for h in groups:
            row[h] = CHANGE_MATRIX.get((g, h), 2)
        rows.append(row)
    return rows
