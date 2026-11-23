(function () {
  function parseIsoDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      return null;
    }
    var parts = value.split("-").map(Number);
    var year = parts[0];
    var month = parts[1];
    var day = parts[2];
    if (year < 1000) {
      return null;
    }
    var dt = new Date(Date.UTC(year, month - 1, day));
    if (
      dt.getUTCFullYear() !== year ||
      dt.getUTCMonth() !== month - 1 ||
      dt.getUTCDate() !== day
    ) {
      return null;
    }
    return dt;
  }

  function addDaysIso(value, days) {
    var dt = parseIsoDate(value);
    if (!dt) {
      return "";
    }
    dt.setUTCDate(dt.getUTCDate() + days);
    return dt.toISOString().slice(0, 10);
  }

  function syncEndDate(startInput, endInput, shouldDefault) {
    var startValue = startInput.value;
    if (!startValue) {
      endInput.removeAttribute("min");
      return;
    }
    if (!parseIsoDate(startValue)) {
      return;
    }
    endInput.min = startValue;
    if (shouldDefault && (!endInput.value || endInput.value < startValue)) {
      var nextDay = addDaysIso(startValue, 1);
      if (nextDay) {
        endInput.value = nextDay;
      }
    }
  }

  function initTripDateForms() {
    var forms = document.querySelectorAll("[data-trip-date-form]");
    forms.forEach(function (form) {
      var startInput = form.querySelector('input[name="start_date"]');
      var endInput = form.querySelector('input[name="end_date"]');
      if (!startInput || !endInput) {
        return;
      }
      syncEndDate(startInput, endInput, false);
      startInput.addEventListener("input", function () {
        syncEndDate(startInput, endInput, true);
      });
      startInput.addEventListener("change", function () {
        syncEndDate(startInput, endInput, true);
      });
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initTripDateForms);
    } else {
      initTripDateForms();
    }
  }
})();
