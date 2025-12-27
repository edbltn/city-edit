export function connectMapStateWebSocket({ url, onState, onStatus }) {
  let ws;

  function setStatus(text) {
    if (onStatus) onStatus(text);
  }

  function connect() {
    setStatus(`connecting to ${url}…`);
    ws = new WebSocket(url);

    ws.onopen = () => setStatus("ws connected");

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "map_state") onState(msg.state);
      } catch (e) {
        console.warn("bad ws message", e);
      }
    };

    ws.onclose = () => {
      setStatus("ws disconnected (retrying…)"); 
      setTimeout(connect, 1000);
    };

    ws.onerror = () => {
      // error will usually be followed by close
      setStatus("ws error");
    };
  }

  connect();

  return {
    send(obj) {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
    }
  };
}
