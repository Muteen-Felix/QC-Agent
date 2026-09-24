import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 10,
  duration: '30s',
  thresholds: {
    http_req_duration: ['p(95)<300'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  const r = http.get(`${__ENV.APP_BASE_URL}/notes`);
  check(r, { 'status 200': (x) => x.status === 200 });
}
