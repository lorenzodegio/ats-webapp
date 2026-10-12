function toggleMostraPassword(inputId, bottone) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const mostrato = input.type === "text";
  input.type = mostrato ? "password" : "text";
  bottone.textContent = mostrato ? "Mostra" : "Nascondi";
  bottone.setAttribute("aria-label", mostrato ? "Mostra password" : "Nascondi password");
}
