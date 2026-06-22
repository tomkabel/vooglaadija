(() => {
  window.Vooglaadija = window.Vooglaadija || {};
  window.Vooglaadija.state = window.Vooglaadija.state || {};
  window.Vooglaadija.events = window.Vooglaadija.events || new EventTarget();
  window.Vooglaadija.init =
    window.Vooglaadija.init ||
    function init(callback) {
      if (typeof callback === 'function') callback(window.Vooglaadija);
      return window.Vooglaadija;
    };
})();
