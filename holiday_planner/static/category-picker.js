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

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initCategoryPickers);
    } else {
      initCategoryPickers();
    }
  }
})();
