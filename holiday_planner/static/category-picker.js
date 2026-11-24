(function () {
  function syncCategoryField(picker, shouldFocus) {
    var select = picker.querySelector('select[name="category_preset"]');
    var customField = picker.querySelector("[data-custom-category-field]");
    if (!select || !customField) {
      return;
    }
    var customInput = customField.querySelector('input[name="category"]');
    var showCustom =
      select.value === "__custom__" ||
      Boolean(customInput && customInput.value.trim());
    customField.hidden = !showCustom;
    if (shouldFocus && showCustom && customInput) {
      customInput.focus();
    }
  }

  function initCategoryPickers() {
    var pickers = document.querySelectorAll("[data-category-picker]");
    pickers.forEach(function (picker) {
      var select = picker.querySelector('select[name="category_preset"]');
      var customField = picker.querySelector("[data-custom-category-field]");
      if (!select || !customField) {
        return;
      }
      var customInput = customField.querySelector('input[name="category"]');
      syncCategoryField(picker, false);
      select.addEventListener("change", function () {
        if (select.value !== "__custom__" && customInput) {
          customInput.value = "";
        }
        syncCategoryField(picker, select.value === "__custom__");
      });
    });
  }

  function initDesktopCaptureLinks() {
    var links = document.querySelectorAll("[data-desktop-focus-capture]");
    links.forEach(function (link) {
      link.addEventListener("click", function (event) {
        var capturePane = document.getElementById("capture-idea");
        var titleInput = document.getElementById("idea-title");
        if (!capturePane || !titleInput) {
          return;
        }
        if (window.getComputedStyle(capturePane).display !== "none") {
          event.preventDefault();
          titleInput.focus();
          if (typeof capturePane.scrollIntoView === "function") {
            capturePane.scrollIntoView({ behavior: "smooth", block: "nearest" });
          }
        }
      });
    });
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initCategoryPickers);
      document.addEventListener("DOMContentLoaded", initDesktopCaptureLinks);
    } else {
      initCategoryPickers();
      initDesktopCaptureLinks();
    }
  }
})();
