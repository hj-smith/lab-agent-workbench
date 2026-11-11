// 只根据当前横轴范围内的可见曲线适配纵轴；不把可编辑锚点和区间色块算进去。
// （工作台的助手 11-11 11:07 按你的要求写的，原样搬回开发版，免得发布时被盖掉）
export function visibleYRange(traces, xRange) {
  const lo = Math.min(...xRange), hi = Math.max(...xRange);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
  let low = Infinity, high = -Infinity;
  const take = (y) => { low = Math.min(low, y); high = Math.max(high, y); };
  for (const trace of traces || []) {
    if (trace.visible === false || trace.visible === 'legendonly' || trace.fitY === false) continue;
    const x = trace.x || [], y = trace.y || [];
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      if (!Number.isFinite(x[i]) || !Number.isFinite(y[i])) continue;
      if (x[i] >= lo && x[i] <= hi) take(y[i]);
      // 横轴窗口比采样间距更小时，也要计入穿过窗口的线段。
      if (!i || !Number.isFinite(x[i - 1]) || !Number.isFinite(y[i - 1]) || x[i] === x[i - 1]) continue;
      for (const edge of [lo, hi]) {
        if (edge >= Math.min(x[i - 1], x[i]) && edge <= Math.max(x[i - 1], x[i])) {
          take(y[i - 1] + (y[i] - y[i - 1]) * (edge - x[i - 1]) / (x[i] - x[i - 1]));
        }
      }
    }
  }
  if (!Number.isFinite(low)) return null;
  const pad = high > low ? (high - low) * 0.08 : Math.max(Math.abs(low) * 0.08, 1);
  return [low - pad, high + pad];
}
