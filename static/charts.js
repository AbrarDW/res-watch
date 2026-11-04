/* res-watch — ECharts / ECharts-GL option builders (2D + 3D). Pure functions. */
'use strict';
(function () {
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  function colors() {
    return {
      text: css('--text'), text2: css('--text-2'), text3: css('--text-3'), grid: css('--grid'), border: css('--border'),
      surface: css('--surface'), ghost: css('--ghost'),
      s: [1, 2, 3, 4, 5, 6, 7, 8].map(i => css('--s' + i)),
      q: [0, 1, 2, 3, 4, 5].map(i => css('--q' + i)),
    };
  }

  function axis(C) {
    return {
      axisLine: {lineStyle: {color: C.border}}, axisTick: {show: false},
      axisLabel: {color: C.text3, fontSize: 11}, splitLine: {lineStyle: {color: C.grid}},
    };
  }
  function tip(C, extra) {
    return Object.assign({trigger: 'axis', confine: true, backgroundColor: C.surface, borderColor: C.border,
      textStyle: {color: C.text, fontSize: 12}}, extra || {});
  }

  /* time axis labels: "-42s" relative to the newest sample */
  function relLabels(times) {
    const last = times.length ? times[times.length - 1] : 0;
    return times.map(t => t === last ? 'now' : `-${Math.round(last - t)}s`);
  }

  /* series: [{name, data, color, dashed, area, stack}] */
  function line(C, times, series, {unit = '', max, fmt} = {}) {
    const A = axis(C);
    return {
      animation: false, grid: {left: 52, right: 14, top: 12, bottom: 24}, tooltip: tip(C, {
        valueFormatter: v => v == null ? '–' : (fmt ? fmt(v) : (Math.round(v * 10) / 10) + unit)}),
      xAxis: {type: 'category', data: relLabels(times), boundaryGap: false, ...A, splitLine: {show: false},
        axisLabel: {...A.axisLabel, interval: Math.max(0, Math.floor(times.length / 4) - 1)}},
      yAxis: {type: 'value', max, min: 0, minInterval: fmt ? 1 : undefined, ...A, axisLine: {show: false},
        axisLabel: {...A.axisLabel, formatter: v => fmt ? fmt(v) : v + unit}},
      series: series.map(s => ({
        type: 'line', name: s.name, data: s.data, showSymbol: false, smooth: 0.3, connectNulls: false,
        color: s.color, stack: s.stack, lineStyle: {width: s.stack ? 1 : 2, type: s.dashed ? 'dashed' : 'solid'},
        areaStyle: s.area ? {opacity: s.stack ? 0.5 : 0.08} : undefined,
      })),
    };
  }

  /* per-core bars: ghost = whole system; entities = [{name, color, values}] stacked on each core */
  function coreBars(C, ghost, entities) {
    const A = axis(C);
    const cats = ghost.map((_, i) => 'C' + i);
    const totals = ghost.map((_, i) => entities.reduce((s, e) => s + (e.values[i] || 0), 0));
    const series = [{name: 'whole system', type: 'bar', data: ghost, barWidth: '58%', z: 1,
      itemStyle: {color: C.ghost, borderRadius: [4, 4, 0, 0]}}];
    entities.forEach((e, k) => {
      const top = k === entities.length - 1;
      series.push({
        name: e.name, type: 'bar', data: e.values, stack: 'sel', z: 2, barWidth: '38%', barGap: '-82%',
        itemStyle: {color: e.color, borderRadius: top ? [3, 3, 0, 0] : 0, borderColor: C.surface, borderWidth: entities.length > 1 ? 0.5 : 0},
        label: top ? {show: ghost.length <= 32, position: 'top', color: C.text2, fontSize: 11,
          formatter: p => totals[p.dataIndex] >= 10 ? Math.round(totals[p.dataIndex]) + '%' : ''} : undefined,
      });
    });
    return {
      animation: false, grid: {left: 44, right: 12, top: 18, bottom: 28},
      tooltip: tip(C, {axisPointer: {type: 'shadow'}, valueFormatter: v => v == null ? '–' : v.toFixed(1) + '%'}),
      xAxis: {type: 'category', data: cats, ...A, splitLine: {show: false},
        axisLabel: {...A.axisLabel, interval: cats.length > 32 ? 3 : cats.length > 16 ? 1 : 0}},
      yAxis: {type: 'value', max: v => Math.max(100, Math.ceil(v.max / 25) * 25), ...A, axisLine: {show: false},
        axisLabel: {...A.axisLabel, formatter: '{value}%'}},
      series,
    };
  }

  /* heatmap: rows[c][t] */
  function heat(C, times, rows) {
    const A = axis(C), data = [];
    rows.forEach((row, c) => row.forEach((v, t) => data.push([t, c, v == null ? '-' : +v.toFixed(1)])));
    const labels = relLabels(times);
    return {
      animation: false, grid: {left: 44, right: 16, top: 8, bottom: 28},
      tooltip: {confine: true, backgroundColor: C.surface, borderColor: C.border, textStyle: {color: C.text},
        formatter: p => `C${p.value[1]} · ${labels[p.value[0]]}: <b>${p.value[2]}%</b>`},
      xAxis: {type: 'category', data: labels, ...A, splitLine: {show: false},
        axisLabel: {...A.axisLabel, interval: Math.max(0, Math.floor(times.length / 6) - 1)}},
      yAxis: {type: 'category', data: rows.map((_, i) => 'C' + i), inverse: true, ...A, splitLine: {show: false},
        axisLabel: {...A.axisLabel, interval: rows.length > 32 ? 3 : rows.length > 16 ? 1 : 0}},
      visualMap: {show: false, min: 0, max: 100, inRange: {color: C.q}},
      series: [{type: 'heatmap', data, itemStyle: {borderColor: C.surface, borderWidth: rows.length > 32 ? 0 : 1}}],
    };
  }

  /* 3D bars: core x time x %, one stacked series per entity: ents = [{name, color, rows[c][t]}] */
  function bars3d(C, times, ents, {autoRotate = false, camera} = {}) {
    const step = times.length > 30 ? 2 : 1;
    const labels = relLabels(times).filter((_, t) => t % step === 0);
    const ncores = ents.length ? ents[0].rows.length : 0;
    const series = ents.map(e => {
      const data = [];  // eslint-disable-line prefer-const
      // bar3D stacks by data index, so every series must keep every (core, time) point in the same order.
      // Dropping zeros (or using '-') shifts or breaks the stack; instead zeros stay as 0 and are made invisible,
      // otherwise they render as flat tiles on the floor and on top of other series.
      e.rows.forEach((row, c) => row.forEach((v, t) => {
        if (t % step) return;
        const z = v == null ? 0 : +v.toFixed(1);
        data.push(z > 0.2 ? {value: [c, t / step, z]} : {value: [c, t / step, 0], itemStyle: {opacity: 0}});
      }));
      return {type: 'bar3D', name: e.name, stack: 'sel', data, shading: 'lambert', bevelSize: 0.15,
        itemStyle: {color: e.color, opacity: 1}, animation: false};
    });
    return {
      tooltip: {formatter: p => `${p.seriesName}<br>C${p.value[0]} · ${labels[p.value[1]]}: <b>${p.value[2]}%</b>`},
      xAxis3D: {type: 'category', name: 'core', data: Array.from({length: ncores}, (_, i) => 'C' + i), axisLabel: {color: C.text3},
        nameTextStyle: {color: C.text2}},
      yAxis3D: {type: 'category', name: 'time', data: labels, axisLabel: {color: C.text3, interval: 9}, nameTextStyle: {color: C.text2}},
      zAxis3D: {type: 'value', name: '%', min: 0, axisLabel: {color: C.text3}, nameTextStyle: {color: C.text2}},
      grid3D: {boxWidth: 150, boxDepth: 110, boxHeight: 60,
        axisLine: {lineStyle: {color: C.text3}}, splitLine: {lineStyle: {color: C.grid}}, axisPointer: {lineStyle: {color: C.text2}},
        viewControl: {alpha: 22, beta: 38, distance: 215, ...(camera || {}), autoRotate, autoRotateSpeed: 6},
        light: {main: {intensity: 1.15, shadow: true, alpha: 40, beta: 30}, ambient: {intensity: 0.35}}},
      series,
    };
  }

  /* 3D surface: lanes{name: values[]} */
  function surface3d(C, times, lanes, {camera} = {}) {
    const names = Object.keys(lanes), data = [];
    names.forEach((k, l) => lanes[k].forEach((v, t) => data.push([t, l, v == null ? 0 : v])));
    return {
      visualMap: {show: false, min: 0, max: 100, inRange: {color: C.q.slice(1)}},
      xAxis3D: {type: 'category', name: 'time', data: relLabels(times), axisLabel: {color: C.text3, interval: 14}},
      yAxis3D: {type: 'category', name: 'engine', data: names, axisLabel: {color: C.text3}},
      zAxis3D: {type: 'value', name: '%', max: 100, min: 0, axisLabel: {color: C.text3}},
      grid3D: {boxWidth: 170, boxDepth: 60, boxHeight: 50, axisLine: {lineStyle: {color: C.text3}},
        splitLine: {lineStyle: {color: C.grid}}, viewControl: {alpha: 22, beta: 30, distance: 210, ...(camera || {})},
        light: {main: {intensity: 1.1}, ambient: {intensity: 0.4}}},
      series: [{type: 'surface', data, wireframe: {show: true, lineStyle: {color: C.surface, width: 0.5}}, shading: 'lambert'}],
    };
  }

  function gauge(C, value, label) {
    return {series: [{type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: 100, radius: '96%', center: ['50%', '58%'],
      progress: {show: true, width: 12, roundCap: true, itemStyle: {color: value > 90 ? css('--crit') : value > 70 ? css('--warn') : C.s[0]}},
      axisLine: {lineStyle: {width: 12, color: [[1, C.grid]]}, roundCap: true},
      axisTick: {show: false}, splitLine: {show: false}, axisLabel: {show: false}, pointer: {show: false},
      title: {offsetCenter: [0, '38%'], color: C.text3, fontSize: 12},
      detail: {offsetCenter: [0, '0%'], formatter: v => v == null ? '–' : `${Math.round(v)}%`, color: C.text, fontSize: 28, fontWeight: 700},
      data: [{value: value == null ? 0 : value, name: label}]}]};
  }

  /* stacked small multiples: lanes{name: values[]} */
  function lanes(C, times, lanesObj, color) {
    const A = axis(C), names = Object.keys(lanesObj), labels = relLabels(times);
    const h = Math.floor(260 / names.length);
    return {
      animation: false, tooltip: tip(C, {valueFormatter: v => v == null ? '–' : Math.round(v) + '%'}),
      grid: names.map((_, i) => ({left: 48, right: 54, top: 8 + i * (h + 10), height: h - 10})),
      xAxis: names.map((_, i) => ({gridIndex: i, type: 'category', data: labels, boundaryGap: false, ...A, splitLine: {show: false},
        axisLabel: {...A.axisLabel, show: i === names.length - 1, interval: Math.max(0, Math.floor(times.length / 4) - 1)}})),
      yAxis: names.map((k, i) => ({gridIndex: i, type: 'value', min: 0, max: 100, splitNumber: 1, ...A, axisLine: {show: false},
        axisLabel: {show: false}, name: k, nameLocation: 'middle', nameRotate: 0, nameGap: 12,
        nameTextStyle: {color: C.text2, fontWeight: 600, fontSize: 11, align: 'right'}})),
      series: names.map((k, i) => ({type: 'line', name: k, xAxisIndex: i, yAxisIndex: i, data: lanesObj[k], showSymbol: false,
        smooth: 0.3, lineStyle: {width: 1.5, color}, areaStyle: {color, opacity: 0.15},
        endLabel: {show: true, color: C.text, fontWeight: 600, formatter: p => p.value == null ? '–' : Math.round(p.value) + '%'}})),
    };
  }

  /* horizontal bars: items [{label, value, color}] */
  function hbars(C, items, {unit = '%', fmt} = {}) {
    const A = axis(C), rev = items.slice().reverse();
    return {
      animation: false, grid: {left: 8, right: 56, top: 6, bottom: 22, containLabel: true},
      tooltip: tip(C, {trigger: 'item', valueFormatter: v => fmt ? fmt(v) : v + unit}),
      xAxis: {type: 'value', min: 0, ...A, axisLabel: {...A.axisLabel, formatter: v => fmt ? fmt(v) : v + unit}},
      yAxis: {type: 'category', data: rev.map(d => d.label), ...A, splitLine: {show: false},
        axisLabel: {color: C.text2, width: 150, overflow: 'truncate'}},
      series: [{type: 'bar', barMaxWidth: 18, data: rev.map(d => ({value: d.value, itemStyle: {color: d.color}})),
        itemStyle: {borderRadius: [0, 4, 4, 0]},
        label: {show: true, position: 'right', color: C.text, formatter: p => fmt ? fmt(p.value) : (Math.round(p.value * 10) / 10) + unit}}],
    };
  }

  function treemap(C, items, color, fmt) {
    return {
      animation: false, tooltip: {formatter: p => `${p.name}: ${fmt(p.value)}`},
      series: [{type: 'treemap', roam: false, nodeClick: false, breadcrumb: {show: false}, width: '100%', height: '100%', top: 0, left: 0,
        itemStyle: {borderColor: C.surface, borderWidth: 2, gapWidth: 2},
        label: {show: true, formatter: p => `{n|${p.name}}\n{v|${fmt(p.value)}}`,
          rich: {n: {color: '#fff', fontSize: 12, fontWeight: 600}, v: {color: 'rgba(255,255,255,.85)', fontSize: 11, padding: [3, 0, 0, 0]}}},
        data: items.map((d, i) => ({name: d.label, value: d.value,
          itemStyle: {color: d.other ? C.text3 : color, opacity: d.other ? 1 : Math.max(0.45, 1 - i * 0.06)}}))}],
    };
  }

  /* tiny inline-SVG sparkline (cheap enough for 64+ cores) */
  function spark(values, color, max = 100) {
    const n = values.length;
    if (n < 2) return '';
    const pts = values.map((v, i) => `${(i / (n - 1) * 100).toFixed(1)},${(26 - Math.min(1, (v || 0) / max) * 24).toFixed(1)}`);
    return `<svg viewBox="0 0 100 26" preserveAspectRatio="none" width="100%" height="26" aria-hidden="true">` +
      `<path d="M0,26 L${pts.join(' L')} L100,26 Z" fill="${color}" fill-opacity=".12"/>` +
      `<path d="M${pts.join(' L')}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`;
  }

  /* several sparklines in one box: lines = [{values, color}] */
  function sparkMulti(lines, max = 100) {
    const n = Math.max(0, ...lines.map(l => l.values.length));
    if (n < 2) return '';
    const path = vals => vals.map((v, i) => `${i ? 'L' : 'M'}${(i / (n - 1) * 100).toFixed(1)},${(26 - Math.min(1, (v || 0) / max) * 24).toFixed(1)}`).join(' ');
    return `<svg viewBox="0 0 100 26" preserveAspectRatio="none" width="100%" height="26" aria-hidden="true">` +
      lines.map(l => `<path d="${path(l.values)}" fill="none" stroke="${l.color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>`).join('') + '</svg>';
  }

  window.RWCharts = {colors, line, coreBars, heat, bars3d, surface3d, gauge, lanes, hbars, treemap, spark, sparkMulti, relLabels};
})();
