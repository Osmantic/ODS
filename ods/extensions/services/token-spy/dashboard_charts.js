(function () {
  const COLORS = {
    text: '#e6edf3',
    muted: '#8b949e',
    grid: '#21262d',
  };

  function getContext(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(canvas.clientWidth || 320, 320);
    const height = Math.max(parseInt(canvas.dataset.height || '', 10) || canvas.clientHeight || 280, 220);
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx, width, height };
  }

  function legend(target, items) {
    if (!target) return;
    target.innerHTML = items.map(item =>
      '<span class="chart-legend-item"><span class="chart-legend-swatch" style="background:' + item.color +
      '"></span><span>' + item.label + '</span></span>'
    ).join('');
  }

  function noData(ctx, width, height) {
    ctx.fillStyle = COLORS.muted;
    ctx.font = '13px system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('No chart data for this time range', width / 2, height / 2);
  }

  function linePath(ctx, points, xFor, yFor, baselineY, fillColor) {
    if (!points.length) return;
    ctx.beginPath();
    ctx.moveTo(xFor(points[0].x), yFor(points[0].y));
    for (let i = 1; i < points.length; i += 1) {
      ctx.lineTo(xFor(points[i].x), yFor(points[i].y));
    }
    if (fillColor) {
      const last = points[points.length - 1];
      const first = points[0];
      ctx.lineTo(xFor(last.x), baselineY);
      ctx.lineTo(xFor(first.x), baselineY);
      ctx.closePath();
      ctx.fillStyle = fillColor;
      ctx.fill();
    }
  }

  function tickXRange(min, max, count) {
    const ticks = [];
    if (!Number.isFinite(min) || !Number.isFinite(max)) return ticks;
    if (min === max) return [min];
    for (let i = 0; i < count; i += 1) {
      ticks.push(min + ((max - min) * i) / (count - 1));
    }
    return ticks;
  }

  function line(canvas, options) {
    const series = options.series || [];
    const legendEl = options.legendEl;

    function draw() {
      const { ctx, width, height } = getContext(canvas);
      const allPoints = series.flatMap(s => s.points || []);
      if (!allPoints.length) {
        legend(legendEl, []);
        noData(ctx, width, height);
        return;
      }

      const margin = { top: 16, right: 14, bottom: 30, left: 56 };
      const plotW = width - margin.left - margin.right;
      const plotH = height - margin.top - margin.bottom;
      const xs = allPoints.map(p => +new Date(p.x));
      const thresholdLines = options.thresholdLines || [];
      const ys = allPoints.map(p => Number(p.y) || 0).concat(thresholdLines.map(t => Number(t.value) || 0));
      let xMin = Math.min(...xs);
      let xMax = Math.max(...xs);
      if (xMin === xMax) xMax = xMin + 1;
      const yMin = options.minY != null ? options.minY : 0;
      let yMax = Math.max(...ys, yMin + 1);
      yMax *= 1.08;

      const xFor = value => margin.left + (((+new Date(value)) - xMin) / (xMax - xMin)) * plotW;
      const yFor = value => margin.top + plotH - (((value - yMin) / (yMax - yMin)) * plotH);

      ctx.strokeStyle = COLORS.grid;
      ctx.fillStyle = COLORS.muted;
      ctx.lineWidth = 1;
      ctx.font = '12px system-ui, sans-serif';

      for (let i = 0; i < 5; i += 1) {
        const value = yMin + ((yMax - yMin) * i) / 4;
        const y = yFor(value);
        ctx.beginPath();
        ctx.moveTo(margin.left, y);
        ctx.lineTo(width - margin.right, y);
        ctx.stroke();
        const label = options.yFormatter ? options.yFormatter(value) : String(Math.round(value));
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText(label, margin.left - 8, y);
      }

      tickXRange(xMin, xMax, 5).forEach(value => {
        const x = xFor(value);
        ctx.beginPath();
        ctx.moveTo(x, margin.top);
        ctx.lineTo(x, height - margin.bottom);
        ctx.stroke();
        const label = options.xFormatter ? options.xFormatter(new Date(value)) : new Date(value).toLocaleTimeString();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(label, x, height - margin.bottom + 8);
      });

      thresholdLines.forEach((lineDef, index) => {
        const y = yFor(lineDef.value);
        ctx.save();
        ctx.setLineDash(index === 0 ? [6, 4] : [4, 4]);
        ctx.strokeStyle = lineDef.color;
        ctx.beginPath();
        ctx.moveTo(margin.left, y);
        ctx.lineTo(width - margin.right, y);
        ctx.stroke();
        ctx.restore();
        ctx.fillStyle = lineDef.color;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'bottom';
        ctx.fillText(lineDef.label, margin.left + 6, y - 4);
        ctx.fillStyle = COLORS.muted;
      });

      series.forEach(s => {
        const points = (s.points || []).filter(p => Number.isFinite(+new Date(p.x)));
        if (!points.length) return;
        if (s.fillColor) {
          linePath(ctx, points, xFor, yFor, margin.top + plotH, s.fillColor);
        }
        ctx.beginPath();
        ctx.moveTo(xFor(points[0].x), yFor(points[0].y));
        for (let i = 1; i < points.length; i += 1) {
          ctx.lineTo(xFor(points[i].x), yFor(points[i].y));
        }
        ctx.strokeStyle = s.color;
        ctx.lineWidth = s.lineWidth || 2;
        ctx.stroke();
        const pointRadius = s.pointRadius == null ? 2 : s.pointRadius;
        if (pointRadius > 0) {
          ctx.fillStyle = s.color;
          points.forEach(point => {
            ctx.beginPath();
            ctx.arc(xFor(point.x), yFor(point.y), pointRadius, 0, Math.PI * 2);
            ctx.fill();
          });
        }
      });

      legend(legendEl, series.map(s => ({ label: s.label, color: s.color })));
    }

    const onResize = () => draw();
    window.addEventListener('resize', onResize);
    draw();
    return {
      destroy() {
        window.removeEventListener('resize', onResize);
        const { ctx, width, height } = getContext(canvas);
        ctx.clearRect(0, 0, width, height);
      },
    };
  }

  function stackedBar(canvas, options) {
    const labels = options.labels || [];
    const datasets = options.datasets || [];
    const legendEl = options.legendEl;

    function draw() {
      const { ctx, width, height } = getContext(canvas);
      if (!labels.length || !datasets.length) {
        legend(legendEl, []);
        noData(ctx, width, height);
        return;
      }

      const margin = { top: 16, right: 14, bottom: 30, left: 56 };
      const plotW = width - margin.left - margin.right;
      const plotH = height - margin.top - margin.bottom;
      const totals = labels.map((_, i) => datasets.reduce((sum, ds) => sum + (Number(ds.data[i]) || 0), 0));
      const yMax = Math.max(...totals, 1) * 1.08;

      ctx.strokeStyle = COLORS.grid;
      ctx.fillStyle = COLORS.muted;
      ctx.lineWidth = 1;
      ctx.font = '12px system-ui, sans-serif';

      for (let i = 0; i < 5; i += 1) {
        const value = (yMax * i) / 4;
        const y = margin.top + plotH - (value / yMax) * plotH;
        ctx.beginPath();
        ctx.moveTo(margin.left, y);
        ctx.lineTo(width - margin.right, y);
        ctx.stroke();
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText(Math.round(value).toLocaleString(), margin.left - 8, y);
      }

      const slot = plotW / labels.length;
      const barWidth = Math.max(Math.min(slot * 0.7, 28), 8);
      const labelEvery = Math.max(Math.ceil(labels.length / 6), 1);

      labels.forEach((labelValue, index) => {
        const x = margin.left + (slot * index) + (slot / 2);
        let stackBase = 0;
        datasets.forEach(ds => {
          const raw = Number(ds.data[index]) || 0;
          if (raw <= 0) return;
          const y = margin.top + plotH - ((stackBase + raw) / yMax) * plotH;
          const barHeight = (raw / yMax) * plotH;
          ctx.fillStyle = ds.color;
          ctx.fillRect(x - (barWidth / 2), y, barWidth, barHeight);
          stackBase += raw;
        });
        if (index % labelEvery === 0 || index === labels.length - 1) {
          const text = options.labelFormatter ? options.labelFormatter(labelValue) : String(labelValue);
          ctx.fillStyle = COLORS.muted;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';
          ctx.fillText(text, x, height - margin.bottom + 8);
        }
      });

      legend(legendEl, datasets.map(ds => ({ label: ds.label, color: ds.color })));
    }

    const onResize = () => draw();
    window.addEventListener('resize', onResize);
    draw();
    return {
      destroy() {
        window.removeEventListener('resize', onResize);
        const { ctx, width, height } = getContext(canvas);
        ctx.clearRect(0, 0, width, height);
      },
    };
  }

  function donut(canvas, options) {
    const labels = options.labels || [];
    const values = options.values || [];
    const colors = options.colors || [];
    const legendEl = options.legendEl;

    function draw() {
      const { ctx, width, height } = getContext(canvas);
      const total = values.reduce((sum, value) => sum + (Number(value) || 0), 0);
      if (!labels.length || total <= 0) {
        legend(legendEl, []);
        noData(ctx, width, height);
        return;
      }

      const centerX = width / 2;
      const centerY = height / 2;
      const radius = Math.min(width, height) * 0.32;
      const innerRadius = radius * 0.58;
      let start = -Math.PI / 2;

      values.forEach((value, index) => {
        const sweep = ((Number(value) || 0) / total) * Math.PI * 2;
        ctx.beginPath();
        ctx.moveTo(centerX, centerY);
        ctx.arc(centerX, centerY, radius, start, start + sweep);
        ctx.closePath();
        ctx.fillStyle = colors[index] || '#58a6ff';
        ctx.fill();
        start += sweep;
      });

      ctx.globalCompositeOperation = 'destination-out';
      ctx.beginPath();
      ctx.arc(centerX, centerY, innerRadius, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalCompositeOperation = 'source-over';

      ctx.fillStyle = COLORS.text;
      ctx.font = '600 14px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(options.centerText || '', centerX, centerY);

      legend(legendEl, labels.map((label, index) => ({ label, color: colors[index] || '#58a6ff' })));
    }

    const onResize = () => draw();
    window.addEventListener('resize', onResize);
    draw();
    return {
      destroy() {
        window.removeEventListener('resize', onResize);
        const { ctx, width, height } = getContext(canvas);
        ctx.clearRect(0, 0, width, height);
      },
    };
  }

  window.TokenSpyCharts = { line, stackedBar, donut };
}());
