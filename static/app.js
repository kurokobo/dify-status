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

    formatTimestamp(ts) {
      if (!ts) return '';
      return ts.replace('T', ' ').replace('Z', ' UTC');
    },
  };
}

function statusApp() {
  return {
    summary: SUMMARY_DATA,
    ...tooltipMixin(),
  };
}

function detailApp() {
  return {
    summary: SUMMARY_DATA,
    checkSummary: SUMMARY_DATA.checks.find(c => c.id === CHECK_ID) || { days: [], current_status: 'nodata' },
    selectedDate: null,
    dayRecords: [],
    hourlyStatus: [],
    loading: false,
    _chart: null,
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

    init() {
      const days = this.checkSummary.days || [];
      const latest = [...days].reverse().find(d => d.status !== 'nodata');
      if (latest) {
        this.selectDate(latest.date);
      }
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

    renderChart(records) {
      const canvas = this.$refs.responseChart;
      if (!canvas) return;

      const filtered = records.filter(r => r.response_time_ms >= 0);
      const labels = filtered.map(r => r.timestamp.substring(11, 16));
      const isKnowledge = this._isKnowledge();
      const data = filtered.map(r => isKnowledge ? r.response_time_ms / 1000 : r.response_time_ms);
      const unit = isKnowledge ? 's' : 'ms';
      const chartLabel = isKnowledge ? 'Indexing Time (s)' : 'Response Time (ms)';

      if (this._chart) {
        this._chart.destroy();
      }

      this._chart = new Chart(canvas, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: chartLabel,
            data: data,
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
          plugins: {
            legend: { display: false },
          },
          scales: {
            x: {
              title: { display: true, text: 'Time (UTC)' },
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

    showHourTooltip(event, h) {
      this.tooltip.visible = true;
      this.tooltip.x = event.clientX + 12;
      this.tooltip.y = event.clientY - 40;
      this.tooltip.date = h.hour + ':00 - ' + h.hour + ':59';
      this.tooltip.name = '';
      this.tooltip.status = h.status;
      this.tooltip.uptime = null;
      this.tooltip.avgResp = -1;
    },
  };
}
