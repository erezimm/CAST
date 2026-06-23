(function () {
  function fallbackCopy(text) {
    var textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "absolute";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
    } finally {
      document.body.removeChild(textarea);
    }
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return Promise.resolve(fallbackCopy(text));
  }

  function handleCopy(event) {
    var button = event.target.closest("[data-copy-text]");
    if (!button) {
      return;
    }
    var text = button.getAttribute("data-copy-text");
    if (!text) {
      return;
    }
    var originalTitle = button.getAttribute("title");
    copyText(text)
      .then(function () {
        if (originalTitle) {
          button.setAttribute("title", "Copied");
          setTimeout(function () {
            button.setAttribute("title", originalTitle);
          }, 1500);
        }
      })
      .catch(function () {
        if (originalTitle) {
          button.setAttribute("title", "Copy failed");
          setTimeout(function () {
            button.setAttribute("title", originalTitle);
          }, 1500);
        }
      });
  }

  document.addEventListener("click", handleCopy);
})();
