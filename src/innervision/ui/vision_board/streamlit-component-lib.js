function sendMessage(type, data) {
  const payload = Object.assign({ isStreamlitMessage: true, type: type }, data);
  window.parent.postMessage(payload, "*");
}

const Streamlit = {
  RENDER_EVENT: "streamlit:render",
  events: {
    addEventListener(type, callback) {
      window.addEventListener("message", (event) => {
        if (event.data && event.data.type === type) {
          callback(event);
        }
      });
    },
  },
  setComponentReady() {
    sendMessage("streamlit:componentReady", { apiVersion: 1 });
  },
  setFrameHeight(height) {
    sendMessage("streamlit:setFrameHeight", { height: height });
  },
  setComponentValue(value) {
    sendMessage("streamlit:setComponentValue", { value: value, dataType: "json" });
  },
};
