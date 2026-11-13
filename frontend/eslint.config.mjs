import next from 'eslint-config-next/core-web-vitals';

export default [...next, {ignores: ['.next*/**', '.test-runtime/**', 'node_modules/**']}];
