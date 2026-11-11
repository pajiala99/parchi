"""Parchi: a serverless, event-driven document processing pipeline on AWS.

A fee receipt, medical bill or invoice is uploaded straight to S3 with a presigned
POST. The upload event lands in SQS, a Lambda function asks Amazon Textract to read
it, the extracted fields are filed in DynamoDB and SNS emails a confirmation. No
server runs at any point.
"""

__version__ = "1.0.0"
