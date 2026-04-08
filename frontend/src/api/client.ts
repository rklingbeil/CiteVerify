import axios from "axios";

const client = axios.create({
  baseURL: "/api",
  timeout: 300_000, // 5 minutes — verification can be slow
});

export default client;
