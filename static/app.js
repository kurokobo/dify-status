function tooltipMixin() {
  return {
    tooltip: {
      visible: false,
      x: 0,
      y: 0,
      date: '',
      name: '',
      status: '',
      uptime: null,
      avgResp: -1,
    },

    showTooltip(event, day) {
      this.tooltip.visible = true;
      this.tooltip.x = event.clientX + 12;
      this.tooltip.y = event.clientY - 40;
      this.tooltip.date = day.date;
      this.tooltip.name = '';
      this.tooltip.status = day.status;
      this.tooltip.uptime = null;
      this.tooltip.avgResp = -1;
    },

    showCheckTooltip(event, day, name) {
      this.tooltip.visible = true;
      this.tooltip.x = event.clientX + 12;
      this.tooltip.y = event.clientY - 40;
      this.tooltip.date = day.date;
      this.tooltip.name = name;
      this.tooltip.status = day.status;
      this.tooltip.uptime = day.uptime_pct !== undefined ? day.uptime_pct : null;
      this.tooltip.avgResp = day.avg_response_ms !== undefined ? day.avg_response_ms : -1;
    },

    hideTooltip() {
      this.tooltip.visible = false;
    },

    showLatestCellTooltip(event, status, name, timestamp, responseMs) {
      this.tooltip.visible = true;
      this.tooltip.x = event.clientX + 12;
      this.tooltip.y = event.clientY - 40;
      this.tooltip.date = timestamp
        ? 'Latest \u00b7 ' + timestamp.substring(0, 16).replace('T', ' ') + ' UTC'
        : 'Latest';
      this.tooltip.name = name || '';
      this.tooltip.status = status;
      this.tooltip.uptime = null;
      this.tooltip.avgResp = (responseMs !== undefined && responseMs >= 0) ? responseMs : -1;
    },

    formatTimestamp(ts) {
      if (!ts) return '';
      return ts.replace('T', ' ').replace('Z', ' UTC');
    },

    localTimeLabel(ts) {
      if (!ts) return '';
      const offset = -new Date().getTimezoneOffset();
      if (offset === 0) return '';
      const abs = Math.abs(offset);
      const h = Math.floor(abs / 60);
      const m = abs % 60;
      const sign = offset >= 0 ? '+' : '-';
      const tz = m > 0
        ? `UTC${sign}${h}:${String(m).padStart(2, '0')}`
        : `UTC${sign}${h}`;
      const d = new Date(ts);
      const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
      return `${timeStr} ${tz}`;
    },
  };
}

function statusApp() {
  return {
    summary: SUMMARY_DATA,
    expandedChecks: {},
    ...tooltipMixin(),

    toggleInfo(checkId) {
      this.expandedChecks[checkId] = !this.expandedChecks[checkId];
    },
  };
}

function _computeTzLabel() {
  const offset = -new Date().getTimezoneOffset();
  const abs = Math.abs(offset);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  const sign = offset >= 0 ? '+' : '-';
  return m > 0
    ? `UTC${sign}${h}:${String(m).padStart(2, '0')}`
    : `UTC${sign}${h}`;
}

function _computeHourLabels(showLocal) {
  return [0, 6, 12, 18, 23].map(utcH => {
    if (!showLocal) return { key: utcH, label: String(utcH), shift: 0 };
    const d = new Date(0);
    d.setUTCHours(utcH);
    const localHour = d.getHours();
    // new Date(0) is UTC Jan 1; local date() of 1=same, 2=+1d, 31=−1d
    const shift = d.getDate() === 1 ? 0 : (d.getDate() === 2 ? 1 : -1);
    return { key: utcH, label: String(localHour), shift };
  });
}

function detailApp() {
  return {
    summary: SUMMARY_DATA,
    checkSummary: SUMMARY_DATA.checks.find(c => c.id === CHECK_ID) || { days: [], current_status: 'nodata' },
    selectedDate: null,
    dayRecords: [],
    hourlyStatus: [],
    loading: false,
    _chartUtc: null,
    _chartLocal: null,
    showLocalTime: false,
    tzLabel: _computeTzLabel(),
    hourLabelsDisplay: _computeHourLabels(false),
    ...tooltipMixin(),

    _isKnowledge() {
      return typeof CHECK_TYPE !== 'undefined' && CHECK_TYPE === 'knowledge';
    },

    formatTime(ms) {
      if (this._isKnowledge()) {
        return (ms / 1000).toFixed(1) + ' s';
      }
      return ms + ' ms';
    },

    _localDayShift(timestamp) {
      const d = new Date(timestamp);
      const utcDate = timestamp.substring(0, 10);
      const localDate = [
        d.getFullYear(),
        String(d.getMonth() + 1).padStart(2, '0'),
        String(d.getDate()).padStart(2, '0'),
      ].join('-');
      if (localDate === utcDate) return 0;
      return Math.round((new Date(localDate) - new Date(utcDate)) / 86400000);
    },

    formatDisplayTime(timestamp) {
      if (!this.showLocalTime) return timestamp.substring(11, 19);
      const d = new Date(timestamp);
      const timePart = d.toLocaleTimeString([], {
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      });
      const shift = this._localDayShift(timestamp);
      if (shift === 0) return timePart;
      return `${timePart} (${shift > 0 ? '+' : ''}${shift}d)`;
    },

    init() {
      const days = this.checkSummary.days || [];
      const latest = [...days].reverse().find(d => d.status !== 'nodata');
      if (latest) {
        this.selectDate(latest.date);
      }
      this.$watch('showLocalTime', () => {
        this.hourLabelsDisplay = _computeHourLabels(this.showLocalTime);
      });
    },

    selectDate(date) {
      if (this.selectedDate === date) return;
      this.selectedDate = date;
      this.loading = true;
      this.dayRecords = [];
      this.hourlyStatus = [];

      fetch(`../data/${CHECK_ID}/${date}.json`)
        .then(r => {
          if (!r.ok) throw new Error(r.status);
          return r.json();
        })
        .then(data => {
          this.dayRecords = data;
          this.hourlyStatus = this.computeHourlyStatus(data);
          this.loading = false;
          this.$nextTick(() => this.renderChart(data));
        })
        .catch(() => {
          this.dayRecords = [];
          this.hourlyStatus = [];
          this.loading = false;
        });
    },

    computeHourlyStatus(records) {
      const hours = [];
      for (let h = 0; h < 24; h++) {
        hours.push({ hour: h, status: 'nodata', count: 0 });
      }
      for (const r of records) {
        const hour = parseInt(r.timestamp.substring(11, 13), 10);
        const bucket = hours[hour];
        bucket.count++;
        if (!bucket._statuses) bucket._statuses = [];
        bucket._statuses.push(r.status);
      }
      for (const bucket of hours) {
        if (bucket.count === 0) continue;
        const statuses = bucket._statuses;
        const downCount = statuses.filter(s => s === 'down').length;
        const downPct = (downCount / statuses.length) * 100;
        if (downPct === 0) {
          bucket.status = statuses.some(s => s === 'degraded') ? 'degraded' : 'up';
        } else if (downPct >= 50) {
          bucket.status = 'down';
        } else {
          bucket.status = 'degraded';
        }
        delete bucket._statuses;
      }
      return hours;
    },

    _updateOrCreateChart(key, canvas, labels, data, unit, chartLabel, xTitle) {
      if (this[key]) {
        this[key].data.labels = labels;
        this[key].data.datasets[0].data = data;
        this[key].options.scales.x.title.text = xTitle;
        this[key].update('none');
        return;
      }
      this[key] = new Chart(canvas, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: chartLabel,
            data,
            borderColor: '#2da44e',
            backgroundColor: 'rgba(45, 164, 78, 0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 2,
            pointHoverRadius: 5,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: {
              title: { display: true, text: xTitle },
              ticks: { maxTicksLimit: 12 },
            },
            y: {
              title: { display: true, text: unit },
              beginAtZero: true,
            },
          },
        },
      });
    },

    renderChart(records) {
      const utcCanvas = this.$refs.responseChartUtc;
      const localCanvas = this.$refs.responseChartLocal;
      if (!utcCanvas || !localCanvas) return;

      const isKnowledge = this._isKnowledge();
      const filtered = records.filter(r => r.response_time_ms >= 0);
      const data = filtered.map(r => isKnowledge ? r.response_time_ms / 1000 : r.response_time_ms);
      const unit = isKnowledge ? 's' : 'ms';
      const chartLabel = isKnowledge ? 'Indexing Time (s)' : 'Response Time (ms)';
      const utcLabels = filtered.map(r => r.timestamp.substring(11, 16));
      const localLabels = filtered.map(r => {
        const d = new Date(r.timestamp);
        const timePart = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        const shift = this._localDayShift(r.timestamp);
        return shift === 0 ? timePart : `${timePart} (${shift > 0 ? '+' : ''}${shift}d)`;
      });

      this._updateOrCreateChart('_chartUtc', utcCanvas, utcLabels, data, unit, chartLabel, 'Time (UTC)');
      this._updateOrCreateChart('_chartLocal', localCanvas, localLabels, data, unit, chartLabel, `Time (${this.tzLabel})`);
    },

    showHourTooltip(event, h) {
      this.tooltip.visible = true;
      this.tooltip.x = event.clientX + 12;
      this.tooltip.y = event.clientY - 40;
      if (this.showLocalTime) {
        const d = new Date(0);
        d.setUTCHours(h.hour);
        const lh = d.getHours();
        this.tooltip.date = `${h.hour}:00-${h.hour}:59 UTC / ${lh}:00-${lh}:59 ${this.tzLabel}`;
      } else {
        this.tooltip.date = `${h.hour}:00 - ${h.hour}:59 (UTC)`;
      }
      this.tooltip.name = '';
      this.tooltip.status = h.status;
      this.tooltip.uptime = null;
      this.tooltip.avgResp = -1;
    },
  };
}
