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
    loading: false,
    ...tooltipMixin(),

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

      fetch(`../data/${CHECK_ID}/${date}.json`)
        .then(r => {
          if (!r.ok) throw new Error(r.status);
          return r.json();
        })
        .then(data => {
          this.dayRecords = data;
          this.loading = false;
        })
        .catch(() => {
          this.dayRecords = [];
          this.loading = false;
        });
    },
  };
}
