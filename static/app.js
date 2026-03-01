/* ===== Theme Management ===== */
const themeManager = {
  _listeners: [],

  getPreference() {
    return localStorage.getItem('theme') || 'system';
  },

  _resolvedTheme() {
    const pref = this.getPreference();
    if (pref === 'light' || pref === 'dark') return pref;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  },

  apply() {
    const resolved = this._resolvedTheme();
    document.documentElement.setAttribute('data-theme', resolved);
    this._listeners.forEach(fn => fn(resolved));
  },

  set(mode) {
    localStorage.setItem('theme', mode);
    this.apply();
  },

  onChange(fn) {
    this._listeners.push(fn);
  },

  init() {
    this.apply();
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (this.getPreference() === 'system') this.apply();
    });
  },
};

themeManager.init();

function themeSwitcher() {
  return {
    current: themeManager.getPreference(),
    set(mode) {
      this.current = mode;
      themeManager.set(mode);
    },
  };
}

function _getChartThemeColors() {
  const style = getComputedStyle(document.documentElement);
  return {
    text: style.getPropertyValue('--color-text-secondary').trim(),
    grid: style.getPropertyValue('--color-border').trim(),
    up: style.getPropertyValue('--color-up').trim(),
    down: style.getPropertyValue('--color-down').trim(),
  };
}

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
      if (timestamp) {
        const d = new Date(timestamp);
        const hh = String(d.getHours()).padStart(2, '0');
        const mm = String(d.getMinutes()).padStart(2, '0');
        this.tooltip.date = `Latest \u00b7 ${hh}:${mm} (${_computeTzLabel()})`;
      } else {
        this.tooltip.date = 'Latest';
      }
      this.tooltip.name = name || '';
      this.tooltip.status = status;
      this.tooltip.uptime = null;
      this.tooltip.avgResp = (responseMs !== undefined && responseMs >= 0) ? responseMs : -1;
    },

    formatTimestamp(ts) {
      if (!ts) return '';
      const utc = ts.substring(0, 16).replace('T', ' ') + ' UTC';
      const offset = -new Date().getTimezoneOffset();
      if (offset === 0) return utc;
      const d = new Date(ts);
      const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
      return `${utc} (${timeStr} ${_computeTzLabel()})`;
    },
  };
}

function statusApp() {
  return {
    summary: { checks: [], overall_days: [], current_overall: '', current_overall_status: 'nodata', last_checked: null, dates: [] },
    _loading: true,
    expandedChecks: {},
    viewMode: localStorage.getItem('viewMode') || '90day',
    hourlyChecks: [],
    overallHourly: [],
    loadingHourly: false,
    _24hLoaded: false,
    multiDayOverall: [],
    multiDayChecks: [],
    loadingMultiDay: false,
    _multiDayLoaded: 0,
    show24hLocalTime: new Date().getTimezoneOffset() !== 0,
    tzLabel: _computeTzLabel(),
    ...tooltipMixin(),

    async init() {
      this.summary = await window._summaryPromise;
      this._loading = false;
      if (this.viewMode !== '90day') {
        this.switchView(this.viewMode);
      }
    },

    toggleInfo(checkId) {
      this.expandedChecks[checkId] = !this.expandedChecks[checkId];
    },

    viewModeHeading() {
      switch (this.viewMode) {
        case '24h': return 'Last 24 Hours';
        case '3d': return 'Last 3 Days';
        case '7d': return 'Last 7 Days';
        case '14d': return 'Last 14 Days';
        default: return '90-Day History';
      }
    },

    async switchView(mode) {
      this.viewMode = mode;
      localStorage.setItem('viewMode', mode);
      if (mode === '24h' && !this._24hLoaded) {
        await this.load24hData();
      } else if (mode === '3d' || mode === '7d' || mode === '14d') {
        const numDays = { '3d': 3, '7d': 7, '14d': 14 }[mode];
        if (this._multiDayLoaded < numDays) {
          await this.loadMultiDayData(numDays);
        }
      }
    },

    _hasDataForDate(dateStr) {
      const day = this.summary.overall_days.find(d => d.date === dateStr);
      return day && day.status !== 'nodata';
    },

    async load24hData() {
      this.loadingHourly = true;
      const now = new Date();
      const nowMs = now.getTime();
      const cutoffMs = nowMs - 24 * 3600 * 1000;
      const todayUtc = now.toISOString().substring(0, 10);
      const yesterdayUtc = new Date(nowMs - 86400000).toISOString().substring(0, 10);

      const fetchPromises = [];
      for (const dateStr of [yesterdayUtc, todayUtc]) {
        if (this._hasDataForDate(dateStr)) {
          fetchPromises.push(
            fetch(`data/daily/${dateStr}.json`)
              .then(r => r.ok ? r.json() : {})
              .catch(() => ({}))
              .then(data => ({ date: dateStr, merged: data }))
          );
        }
      }
      const results = await Promise.all(fetchPromises);

      const recordsByCheck = {};
      for (const { merged } of results) {
        for (const [checkId, records] of Object.entries(merged)) {
          if (!recordsByCheck[checkId]) recordsByCheck[checkId] = [];
          recordsByCheck[checkId].push(...records);
        }
      }

      const allCheckHourly = [];
      for (const check of this.summary.checks) {
        const allRecords = recordsByCheck[check.id] || [];
        const recent = allRecords.filter(r => new Date(r.t).getTime() >= cutoffMs);
        const hours = this._compute24hBuckets(recent, now);
        allCheckHourly.push({
          id: check.id,
          name: check.name,
          description: check.description,
          note: check.note,
          current_status: check.current_status,
          latest_timestamp: check.latest_timestamp,
          latest_response_ms: check.latest_response_ms,
          hours,
        });
      }

      const overallHours = [];
      for (let i = 0; i < 24; i++) {
        const checkStatuses = allCheckHourly
          .map(c => c.hours[i].status)
          .filter(s => s !== 'nodata');
        let status = 'nodata';
        if (checkStatuses.length > 0) {
          if (checkStatuses.some(s => s === 'down')) status = 'down';
          else if (checkStatuses.some(s => s === 'degraded')) status = 'degraded';
          else status = 'up';
        }
        const hourDate = new Date(nowMs - (23 - i) * 3600000);
        overallHours.push({
          localHour: hourDate.getHours(),
          utcHour: hourDate.getUTCHours(),
          isYesterdayLocal: hourDate.getDate() !== now.getDate() || hourDate.getMonth() !== now.getMonth(),
          isYesterdayUtc: hourDate.getUTCDate() !== now.getUTCDate() || hourDate.getUTCMonth() !== now.getUTCMonth(),
          status,
          count: checkStatuses.length,
        });
      }

      this.hourlyChecks = allCheckHourly;
      this.overallHourly = overallHours;
      this.loadingHourly = false;
      this._24hLoaded = true;
    },

    _compute24hBuckets(records, now) {
      const nowMs = now.getTime();
      const hours = [];
      for (let i = 0; i < 24; i++) {
        const hourDate = new Date(nowMs - (23 - i) * 3600000);
        hours.push({
          localHour: hourDate.getHours(),
          utcHour: hourDate.getUTCHours(),
          isYesterdayLocal: hourDate.getDate() !== now.getDate() || hourDate.getMonth() !== now.getMonth(),
          isYesterdayUtc: hourDate.getUTCDate() !== now.getUTCDate() || hourDate.getUTCMonth() !== now.getUTCMonth(),
          status: 'nodata',
          count: 0,
          _statuses: [],
        });
      }
      for (const r of records) {
        const rTime = new Date(r.t);
        const hoursAgo = Math.floor((nowMs - rTime.getTime()) / 3600000);
        if (hoursAgo >= 0 && hoursAgo < 24) {
          const idx = 23 - hoursAgo;
          hours[idx].count++;
          hours[idx]._statuses.push(r.s);
        }
      }
      for (const bucket of hours) {
        if (bucket.count === 0) continue;
        const statuses = bucket._statuses;
        const downCount = statuses.filter(s => s === 'down').length;
        if (downCount === 0) {
          bucket.status = statuses.some(s => s === 'degraded') ? 'degraded' : 'up';
        } else if (downCount / statuses.length >= 0.5) {
          bucket.status = 'down';
        } else {
          bucket.status = 'degraded';
        }
        delete bucket._statuses;
      }
      return hours;
    },

    hourLabels24h() {
      if (!this.overallHourly || this.overallHourly.length === 0) return [];
      return [0, 6, 12, 18, 23].map(idx => {
        const h = this.overallHourly[idx];
        const isLocal = this.show24hLocalTime;
        const isYesterday = isLocal ? h.isYesterdayLocal : h.isYesterdayUtc;
        return {
          key: idx,
          label: String(isLocal ? h.localHour : h.utcHour).padStart(2, '0') + ':00',
          shift: isYesterday ? -1 : 0,
        };
      });
    },

    // --- Multi-day (3D/7D) ---

    async loadMultiDayData(numDays) {
      this.loadingMultiDay = true;
      const now = new Date();
      const nowMs = now.getTime();

      const dates = [];
      for (let i = numDays - 1; i >= 0; i--) {
        dates.push(new Date(nowMs - i * 86400000).toISOString().substring(0, 10));
      }

      const fetchPromises = [];
      for (const dateStr of dates) {
        if (this._hasDataForDate(dateStr)) {
          fetchPromises.push(
            fetch(`data/daily/${dateStr}.json`)
              .then(r => r.ok ? r.json() : {})
              .catch(() => ({}))
              .then(data => ({ date: dateStr, merged: data }))
          );
        }
      }
      const results = await Promise.all(fetchPromises);

      const recordsByCheckDate = {};
      for (const { date, merged } of results) {
        for (const [checkId, records] of Object.entries(merged)) {
          if (!recordsByCheckDate[checkId]) recordsByCheckDate[checkId] = {};
          recordsByCheckDate[checkId][date] = records;
        }
      }

      const allCheckMultiDay = [];
      for (const check of this.summary.checks) {
        const days = [];
        for (const dateStr of dates) {
          const records = (recordsByCheckDate[check.id] || {})[dateStr] || [];
          const hours = this._computeDayHourlyBuckets(records);
          days.push({ date: dateStr, hours, dayStatus: this._summarizeDayStatus(hours, true) });
        }
        allCheckMultiDay.push({
          id: check.id,
          name: check.name,
          description: check.description,
          note: check.note,
          current_status: check.current_status,
          latest_timestamp: check.latest_timestamp,
          latest_response_ms: check.latest_response_ms,
          days,
        });
      }

      const overallMultiDay = [];
      for (let d = 0; d < dates.length; d++) {
        const hours = [];
        for (let h = 0; h < 24; h++) {
          const checkStatuses = allCheckMultiDay
            .map(c => c.days[d].hours[h].status)
            .filter(s => s !== 'nodata');
          let status = 'nodata';
          if (checkStatuses.length > 0) {
            if (checkStatuses.some(s => s === 'down')) status = 'down';
            else if (checkStatuses.some(s => s === 'degraded')) status = 'degraded';
            else status = 'up';
          }
          hours.push({ utcHour: h, status });
        }
        const dayStatus = this._summarizeDayStatus(hours, true);
        overallMultiDay.push({ date: dates[d], hours, dayStatus });
      }

      this.multiDayOverall = overallMultiDay;
      this.multiDayChecks = allCheckMultiDay;
      this._multiDayLoaded = numDays;
      this.loadingMultiDay = false;
    },

    _computeDayHourlyBuckets(records) {
      const hours = [];
      for (let h = 0; h < 24; h++) {
        hours.push({ utcHour: h, status: 'nodata', count: 0, _statuses: [] });
      }
      for (const r of records) {
        const hour = parseInt(r.t.substring(11, 13), 10);
        if (hour >= 0 && hour < 24) {
          hours[hour].count++;
          hours[hour]._statuses.push(r.s);
        }
      }
      for (const bucket of hours) {
        if (bucket.count === 0) continue;
        const statuses = bucket._statuses;
        const downCount = statuses.filter(s => s === 'down').length;
        if (downCount === 0) {
          bucket.status = statuses.some(s => s === 'degraded') ? 'degraded' : 'up';
        } else if (downCount / statuses.length >= 0.5) {
          bucket.status = 'down';
        } else {
          bucket.status = 'degraded';
        }
        delete bucket._statuses;
      }
      return hours;
    },

    _summarizeDayStatus(hours, useThreshold) {
      const statuses = hours.map(h => h.status).filter(s => s !== 'nodata');
      if (statuses.length === 0) return 'nodata';
      if (useThreshold) {
        const downCount = statuses.filter(s => s === 'down').length;
        if (downCount === 0) {
          return statuses.some(s => s === 'degraded') ? 'degraded' : 'up';
        }
        return downCount / statuses.length >= 0.5 ? 'down' : 'degraded';
      }
      if (statuses.some(s => s === 'down')) return 'down';
      if (statuses.some(s => s === 'degraded')) return 'degraded';
      return 'up';
    },

    multiDaySlice(arr) {
      const n = { '3d': 3, '7d': 7, '14d': 14 }[this.viewMode] || 7;
      return arr.slice(-n);
    },

    _multiDayLastIdx() {
      const n = { '3d': 3, '7d': 7, '14d': 14 }[this.viewMode] || 7;
      return Math.min(n, this.multiDayOverall.length) - 1;
    },

    formatShortDate(dateStr) {
      const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const parts = dateStr.split('-');
      return months[parseInt(parts[1], 10) - 1] + ' ' + parseInt(parts[2], 10);
    },

    formatShortDateDow(dateStr) {
      const dow = ['Su','Mo','Tu','We','Th','Fr','Sa'];
      const d = new Date(dateStr + 'T00:00:00Z');
      return this.formatShortDate(dateStr) + ' ' + dow[d.getUTCDay()];
    },

    dayOfWeek(dateStr) {
      const dow = ['Su','Mo','Tu','We','Th','Fr','Sa'];
      const d = new Date(dateStr + 'T00:00:00Z');
      return dow[d.getUTCDay()];
    },

    ninetyDayLabels(days) {
      if (!days || days.length < 2) return [];
      const last = days.length - 1;
      const indices = [0];
      for (let i = 30; i < last; i += 30) indices.push(i);
      indices.push(last);
      return indices.map((idx, i, arr) => ({
        label: this.formatShortDate(days[idx].date),
        isFirst: i === 0,
        isLast: i === arr.length - 1,
      }));
    },

    multiDayHourLabels() {
      return _computeHourLabels(this.show24hLocalTime);
    },

    showDaySummaryTooltip(event, day, name) {
      this.tooltip.visible = true;
      this.tooltip.x = event.clientX + 12;
      this.tooltip.y = event.clientY - 40;
      this.tooltip.date = this.formatShortDate(day.date) + ' (daily)';
      this.tooltip.name = name || '';
      this.tooltip.status = day.dayStatus;
      this.tooltip.uptime = null;
      this.tooltip.avgResp = -1;
    },

    showMultiDayTooltip(event, h, dateStr, name) {
      this.tooltip.visible = true;
      this.tooltip.x = event.clientX + 12;
      this.tooltip.y = event.clientY - 40;
      const utcH = h.utcHour;
      const hStr = String(utcH).padStart(2, '0');
      if (this.show24hLocalTime) {
        const d = new Date(dateStr + 'T' + hStr + ':00:00Z');
        const localH = String(d.getHours()).padStart(2, '0');
        this.tooltip.date = this.formatShortDate(dateStr) + ', ' + localH + ':00\u2013' + localH + ':59 (' + _computeTzLabel() + ')';
      } else {
        this.tooltip.date = this.formatShortDate(dateStr) + ', ' + hStr + ':00\u2013' + hStr + ':59 (UTC)';
      }
      this.tooltip.name = name || '';
      this.tooltip.status = h.status;
      this.tooltip.uptime = null;
      this.tooltip.avgResp = -1;
    },

    show24hLatestTooltip(event, status, name, timestamp, responseMs) {
      this.tooltip.visible = true;
      this.tooltip.x = event.clientX + 12;
      this.tooltip.y = event.clientY - 40;
      if (timestamp) {
        if (this.show24hLocalTime) {
          const d = new Date(timestamp);
          const hh = String(d.getHours()).padStart(2, '0');
          const mm = String(d.getMinutes()).padStart(2, '0');
          this.tooltip.date = `Latest \u00b7 ${hh}:${mm} (${_computeTzLabel()})`;
        } else {
          this.tooltip.date = 'Latest \u00b7 ' + timestamp.substring(0, 16).replace('T', ' ') + ' UTC';
        }
      } else {
        this.tooltip.date = 'Latest';
      }
      this.tooltip.name = name || '';
      this.tooltip.status = status;
      this.tooltip.uptime = null;
      this.tooltip.avgResp = (responseMs !== undefined && responseMs >= 0) ? responseMs : -1;
    },

    show24hTooltip(event, h, name) {
      this.tooltip.visible = true;
      this.tooltip.x = event.clientX + 12;
      this.tooltip.y = event.clientY - 40;
      const hour = this.show24hLocalTime ? h.localHour : h.utcHour;
      const hStr = String(hour).padStart(2, '0');
      const tz = this.show24hLocalTime ? _computeTzLabel() : 'UTC';
      this.tooltip.date = `${hStr}:00\u2013${hStr}:59 (${tz})`;
      this.tooltip.name = name || '';
      this.tooltip.status = h.status;
      this.tooltip.uptime = null;
      this.tooltip.avgResp = -1;
    },
  };
}

function _computeTzLabel() {
  const offset = -new Date().getTimezoneOffset();
  if (offset === 0) return 'UTC';
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
    if (!showLocal) return { key: utcH, label: utcH + ':00', shift: 0 };
    const d = new Date(0);
    d.setUTCHours(utcH);
    const localHour = d.getHours();
    // new Date(0) is UTC Jan 1; local date() of 1=same, 2=+1d, 31=−1d
    const shift = d.getDate() === 1 ? 0 : (d.getDate() === 2 ? 1 : -1);
    return { key: utcH, label: localHour + ':00', shift };
  });
}

function detailApp() {
  return {
    checkSummary: CHECK_SUMMARY || { days: [], current_status: 'nodata' },
    selectedDate: null,
    dayRecords: [],
    hourlyStatus: [],
    loading: false,
    hasLoadedData: false,
    _chartUtc: null,
    _chartLocal: null,
    showLocalTime: new Date().getTimezoneOffset() !== 0,
    tzLabel: _computeTzLabel(),
    hourLabelsDisplay: _computeHourLabels(new Date().getTimezoneOffset() !== 0),
    ...tooltipMixin(),

    formatShortDate(dateStr) {
      const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const parts = dateStr.split('-');
      return months[parseInt(parts[1], 10) - 1] + ' ' + parseInt(parts[2], 10);
    },

    ninetyDayLabels(days) {
      if (!days || days.length < 2) return [];
      const last = days.length - 1;
      const indices = [0];
      for (let i = 30; i < last; i += 30) indices.push(i);
      indices.push(last);
      return indices.map((idx, i, arr) => ({
        label: this.formatShortDate(days[idx].date),
        isFirst: i === 0,
        isLast: i === arr.length - 1,
      }));
    },

    _availableDates() {
      const dates = (this.checkSummary.days || []).map(d => d.date);
      const latestDate = this.checkSummary.latest_timestamp
        ? this.checkSummary.latest_timestamp.substring(0, 10)
        : null;
      if (latestDate && (dates.length === 0 || dates[dates.length - 1] !== latestDate)) {
        dates.push(latestDate);
      }
      return dates;
    },

    prevDate() {
      const dates = this._availableDates();
      const idx = dates.indexOf(this.selectedDate);
      return idx > 0 ? dates[idx - 1] : null;
    },

    nextDate() {
      const dates = this._availableDates();
      const idx = dates.indexOf(this.selectedDate);
      return (idx >= 0 && idx < dates.length - 1) ? dates[idx + 1] : null;
    },

    _useSeconds() {
      return typeof CHECK_TYPE === 'undefined' || CHECK_TYPE !== 'webhook';
    },

    formatTime(ms) {
      if (this._useSeconds()) {
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
      const hash = window.location.hash.substring(1);
      const dates = this._availableDates();
      if (hash && dates.includes(hash)) {
        this.selectDate(hash);
      } else {
        const days = this.checkSummary.days || [];
        const latest = [...days].reverse().find(d => d.status !== 'nodata');
        if (latest) {
          this.selectDate(latest.date);
        }
      }
      this.$watch('showLocalTime', () => {
        this.hourLabelsDisplay = _computeHourLabels(this.showLocalTime);
      });
      themeManager.onChange(() => {
        if (this.dayRecords.length > 0) {
          this._destroyCharts();
          this.$nextTick(() => this.renderChart(this.dayRecords));
        }
      });
    },

    _destroyCharts() {
      if (this._chartUtc) { this._chartUtc.destroy(); this._chartUtc = null; }
      if (this._chartLocal) { this._chartLocal.destroy(); this._chartLocal = null; }
    },

    _hasCheckDataForDate(dateStr) {
      const day = (this.checkSummary.days || []).find(d => d.date === dateStr);
      if (day && day.status !== 'nodata') return true;
      const latestDate = this.checkSummary.latest_timestamp
        ? this.checkSummary.latest_timestamp.substring(0, 10)
        : null;
      return dateStr === latestDate;
    },

    selectDate(date) {
      if (this.selectedDate === date) return;
      this._destroyCharts();
      this.selectedDate = date;

      if (!this._hasCheckDataForDate(date)) {
        this.dayRecords = [];
        this.hourlyStatus = [];
        this.hasLoadedData = false;
        this.loading = false;
        return;
      }

      this.loading = true;

      if (this._fetchController) this._fetchController.abort();
      const controller = new AbortController();
      this._fetchController = controller;

      fetch(`../data/${CHECK_ID}/${date}.json`, { signal: controller.signal })
        .then(r => {
          if (!r.ok) throw new Error(r.status);
          return r.json();
        })
        .then(data => {
          if (controller.signal.aborted) return;
          this.dayRecords = data;
          this.hourlyStatus = this.computeHourlyStatus(data);
          this.hasLoadedData = data.length > 0;
          this.loading = false;
          this.$nextTick(() => this.renderChart(data));
        })
        .catch(err => {
          if (err.name === 'AbortError') return;
          this.dayRecords = [];
          this.hourlyStatus = [];
          this.hasLoadedData = false;
          this.loading = false;
        });
    },

    computeHourlyStatus(records) {
      const hours = [];
      for (let h = 0; h < 24; h++) {
        hours.push({ hour: h, status: 'nodata', count: 0 });
      }
      for (const r of records) {
        const hour = parseInt(r.t.substring(11, 13), 10);
        const bucket = hours[hour];
        bucket.count++;
        if (!bucket._statuses) bucket._statuses = [];
        bucket._statuses.push(r.s);
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

    _updateOrCreateChart(key, canvas, labels, data, statuses, unit, chartLabel, xTitle) {
      const theme = _getChartThemeColors();
      const pointColors = statuses.map(s => s === 'up' ? theme.up : theme.down);
      this[key] = new Chart(canvas, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: chartLabel,
            data,
            borderColor: theme.up,
            backgroundColor: 'rgba(45, 164, 78, 0.1)',
            spanGaps: true,
            segment: {
              borderColor: ctx => {
                if (ctx.p0.skip || ctx.p1.skip) return theme.down;
                return (statuses[ctx.p0DataIndex] !== 'up' || statuses[ctx.p1DataIndex] !== 'up') ? theme.down : theme.up;
              },
              borderDash: ctx => (ctx.p0.skip || ctx.p1.skip) ? [5, 5] : [],
            },
            fill: false,
            tension: 0.3,
            pointRadius: 2,
            pointHoverRadius: 5,
            pointBackgroundColor: pointColors,
            pointBorderColor: pointColors,
          }],
        },
        options: {
          animation: false,
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: {
              title: { display: true, text: xTitle, color: theme.text },
              ticks: { maxTicksLimit: 12, color: theme.text },
              grid: { color: theme.grid },
            },
            y: {
              title: { display: true, text: unit, color: theme.text },
              beginAtZero: true,
              ticks: { color: theme.text },
              grid: { color: theme.grid },
            },
          },
        },
      });
    },

    renderChart(records) {
      const utcCanvas = this.$refs.responseChartUtc;
      const localCanvas = this.$refs.responseChartLocal;
      if (!utcCanvas || !localCanvas) return;

      const useSeconds = this._useSeconds();
      const unit = useSeconds ? 's' : 'ms';
      const chartLabel = useSeconds ? 'Response Time (s)' : 'Response Time (ms)';
      // Use null only when response_time_ms is unavailable; show DOWN points with response times in red
      const data = records.map(r => r.r >= 0 ? (useSeconds ? r.r / 1000 : r.r) : null);
      const statuses = records.map(r => r.s);
      const utcLabels = records.map(r => r.t.substring(11, 16));
      const localLabels = records.map(r => {
        const d = new Date(r.t);
        const timePart = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        const shift = this._localDayShift(r.t);
        return shift === 0 ? timePart : `${timePart} (${shift > 0 ? '+' : ''}${shift}d)`;
      });

      this._updateOrCreateChart('_chartUtc', utcCanvas, utcLabels, data, statuses, unit, chartLabel, 'Time (UTC)');
      this._updateOrCreateChart('_chartLocal', localCanvas, localLabels, data, statuses, unit, chartLabel, `Time (${this.tzLabel})`);
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
