// Package bucket stores call recordings in the S3 bucket (decisions 0008, 0015, 0026), through the
// MinIO Go client, so any S3-compatible store works: SeaweedFS in Compose, AWS S3, Lightsail, R2.
//
// Rule: a recording's object key is made here and only here, under recordings/YYYY/MM/DD/ by the
// UTC day web stored it, so retention removes whole days without reading Postgres.
package bucket
