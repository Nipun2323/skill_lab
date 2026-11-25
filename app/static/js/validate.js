/* Client-side validation. The server repeats every check; this only gives faster feedback. */
(function () {
  "use strict";

  var EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s.]+$/;

  var rules = {
    email: function (v) {
      v = v.trim();
      if (!v) return "Enter your email address.";
      if (v.length > 254) return "Email must be 254 characters or fewer.";
      if (!EMAIL_RE.test(v)) return "Enter a valid email address, like name@example.com.";
      return "";
    },
    newPassword: function (v) {
      if (!v) return "Enter a password.";
      if (v.length < 8) return "Password needs at least 8 characters.";
      if (v.length > 128) return "Password must be 128 characters or fewer.";
      if (!/[A-Za-z]/.test(v)) return "Password needs at least one letter.";
      if (!/[0-9]/.test(v)) return "Password needs at least one number.";
      return "";
    },
    required: function (label) {
      return function (v) { return v ? "" : label; };
    }
  };

  function setError(input, message) {
    var id = input.getAttribute("aria-describedby") || "";
    var errId = id.split(" ").filter(function (x) { return /-error$/.test(x); })[0];
    var el = errId && document.getElementById(errId);
    if (el) el.textContent = message;
    if (message) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  }

  function check(form, checks) {
    var first = null;
    checks.forEach(function (c) {
      var input = form.querySelector(c[0]);
      if (!input) return;
      var msg = c[1](input.value, form);
      setError(input, msg);
      if (msg && !first) first = input;
    });
    if (first) first.focus();
    return !first;
  }

  var formChecks = {
    login: [
      ["#email", rules.required("Enter your email address.")],
      ["#password", rules.required("Enter your password.")]
    ],
    register: [
      ["#email", rules.email],
      ["#password", rules.newPassword],
      ["#confirm", function (v, form) {
        if (!v) return "Confirm your password.";
        return v === form.querySelector("#password").value ? "" : "Passwords don't match.";
      }]
    ],
    "confirm-password": [
      ["input[type=password]", rules.required("Enter your password to confirm.")]
    ]
  };

  document.querySelectorAll("form[data-validate]").forEach(function (form) {
    var checks = formChecks[form.getAttribute("data-validate")];
    if (!checks) return;
    form.addEventListener("submit", function (e) {
      if (!check(form, checks)) e.preventDefault();
    });
    // Clear a field's message as soon as it becomes valid
    checks.forEach(function (c) {
      var input = form.querySelector(c[0]);
      if (!input) return;
      input.addEventListener("input", function () {
        if (input.getAttribute("aria-invalid") === "true" && !c[1](input.value, form)) setError(input, "");
      });
    });
  });

  window.SMValidate = { setError: setError, rules: rules };
})();
