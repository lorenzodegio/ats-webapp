/* app.js — logica condivisa su tutte le pagine */
document.addEventListener("click", function (evento) {
  document.querySelectorAll("details.sidebar-user[open]").forEach(function (menu) {
    if (!menu.contains(evento.target)) {
      menu.removeAttribute("open");
    }
  });
});
