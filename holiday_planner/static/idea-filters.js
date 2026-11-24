(function () {
  function initIdeaFilters() {
    var forms = document.querySelectorAll("[data-idea-filter-form]");
    forms.forEach(function (form) {
      var controls = form.querySelectorAll('select[name="status"], select[name="category"]');
      controls.forEach(function (control) {
        control.addEventListener("change", function () {
          if (typeof form.requestSubmit === "function") {
            form.requestSubmit();
          } else {
            form.submit();
          }
        });
      });
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initIdeaFilters);
    } else {
      initIdeaFilters();
    }
  }
})();
