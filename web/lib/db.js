// Read-only access to the Neon runs table written by pipeline.py (db.save_run).
const { Pool } = require("pg");

let pool;

function getPool() {
  if (!pool) {
    pool = new Pool({
      connectionString: process.env.NEON_DATABASE_URL || process.env.DATABASE_URL,
      max: 1,
    });
  }
  return pool;
}

module.exports = { getPool };
