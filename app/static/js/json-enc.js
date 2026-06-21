(() => {
  htmx.defineExtension('json-enc', {
    onEvent: (name, evt) => {
      if (name === 'htmx:configRequest') {
        evt.detail.headers['Content-Type'] = 'application/json';
      }
    },
    encodeParameters: (_xhr, formData, _elt) => {
      const obj = {};
      for (const e of formData.entries()) {
        obj[e[0]] = e[1];
      }
      return JSON.stringify(obj);
    },
  });
})();
